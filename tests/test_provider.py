from __future__ import annotations

import base64
import json
import traceback

import httpx
import pytest
from pydantic import SecretStr

from learnmargin import provider as provider_module
from learnmargin.models import APIConfig, Model
from learnmargin.provider import Provider, ProviderError, decode_json


class Reply(Model):
    answer: str


def envelope(protocol="chat_completions", text='{"answer":"已生成"}'):
    if protocol == "responses":
        return {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": text}]}], "usage": {"input_tokens": 8, "output_tokens": 3}}
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3, "details": {"cached": 1}}}


def config(protocol="chat_completions", **kwargs):
    return APIConfig(protocol=protocol, base_url="https://model.example/v1", model="test-model",
                     api_key=SecretStr("fake-test-key"), **kwargs)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_protocol_json_and_local_image_payloads(protocol, tmp_path):
    picture = tmp_path / "source.png"
    picture.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6lU4AAAAASUVORK5CYII="))
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=envelope(protocol))

    async with Provider(config(protocol), transport=httpx.MockTransport(respond)) as client:
        reply = await client.generate(Reply, "教学任务", "课程正文", [picture])
        assert reply.answer == "已生成"
        assert len(client.usage) == 1
        assert all(isinstance(value, (int, float)) for value in client.usage[0].values())
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["authorization"] == "Bearer fake-test-key"
    payload = json.loads(request.content)
    assert payload["model"] == "test-model"
    if protocol == "responses":
        assert request.url.path == "/v1/responses"
        assert payload["store"] is False
        assert payload["text"]["format"]["type"] == "json_object"
        content = payload["input"][0]["content"]
        assert content[0] == {"type": "input_text", "text": "课程正文"}
        assert content[1]["type"] == "input_image"
        image_url = content[1]["image_url"]
    else:
        assert request.url.path == "/v1/chat/completions"
        assert payload["response_format"]["type"] == "json_object"
        content = payload["messages"][1]["content"]
        assert content[0] == {"type": "text", "text": "课程正文"}
        assert content[1]["type"] == "image_url"
        image_url = content[1]["image_url"]["url"]
    assert base64.b64decode(image_url.split(",", 1)[1]) == picture.read_bytes()
    assert str(tmp_path) not in request.content.decode()


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_json_mode_can_be_disabled_for_compatible_servers(protocol):
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=envelope(protocol))

    async with Provider(config(protocol, json_mode=False), transport=httpx.MockTransport(respond)) as client:
        await client.generate(Reply, "系统", "正文")
    assert "response_format" not in sent[0]
    assert "text" not in sent[0]


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_invalid_model_json_gets_only_one_repair(protocol):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=envelope(protocol, "not valid json"))

    async with Provider(config(protocol), transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match="两次"):
            await client.generate(Reply, "系统", "正文")
    assert len(requests) == 2
    assert "上次输出未通过结构校验" in json.dumps(requests[1], ensure_ascii=False)


async def test_invalid_schema_is_repaired_and_fenced_json_is_accepted():
    responses = iter([envelope(text='{"wrong":"shape"}'), envelope(text='```json\n{"answer":"ok"}\n```')])
    async with Provider(config(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=next(responses)))) as client:
        reply = await client.generate(Reply, "系统", "正文")
    assert reply.answer == "ok"


@pytest.mark.parametrize("status", [401, 403])
async def test_authentication_failure_does_not_retry_or_expose_server_body(status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "fake-test-key is bad; private provider detail"})

    async with Provider(config(), transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match="鉴权") as caught:
            await client.generate(Reply, "系统", "正文")
    assert len(requests) == 1
    assert "fake-test-key" not in str(caught.value)
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_truncated_output_is_not_used_as_a_valid_lesson(protocol):
    requests = []
    response = envelope(protocol)
    if protocol == "responses":
        response["status"] = "incomplete"
    else:
        response["choices"][0]["finish_reason"] = "length"

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    async with Provider(config(protocol), transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match="未完整|截断"):
            await client.generate(Reply, "系统", "正文")
    assert len(requests) == 1


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_transient_errors_have_bounded_retries_without_real_waits(status, monkeypatch):
    requests, delays = [], []

    async def sleep(delay):
        delays.append(delay)

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "test"})

    monkeypatch.setattr(provider_module.asyncio, "sleep", sleep)
    async with Provider(config(), transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError):
            await client.generate(Reply, "系统", "正文")
    assert len(requests) == 3
    assert delays == [2, 4]


async def test_timeout_is_clear_and_not_automatically_retried():
    requests = []

    def respond(request):
        requests.append(request)
        raise httpx.ReadTimeout("test timeout", request=request)

    async with Provider(config(), transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match="超时"):
            await client.generate(Reply, "系统", "正文")
    assert len(requests) == 1


async def test_redirect_cannot_forward_authorization_to_another_host():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://another.example/receive"})

    async with Provider(config(), transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match="HTTP 302"):
            await client.generate(Reply, "系统", "正文")
    assert len(requests) == 1
    assert requests[0].url.host == "model.example"


@pytest.mark.parametrize("protocol,response", [
    ("chat_completions", {"choices": ["invalid"]}),
    ("chat_completions", {"choices": [{"message": []}]}),
    ("chat_completions", {"choices": [], "usage": ["invalid"]}),
    ("responses", {"output": ["invalid"]}),
    ("responses", {"output": [{"type": "message", "content": ["invalid"]}]}),
])
async def test_malformed_provider_envelope_raises_actionable_error(protocol, response):
    async with Provider(config(protocol), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response))) as client:
        with pytest.raises(ProviderError):
            await client.generate(Reply, "系统", "正文")


@pytest.mark.parametrize("text", ["[]", "null", "1", '"text"'])
def test_model_result_must_be_a_json_object(text):
    with pytest.raises(ValueError):
        decode_json(text)


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


async def test_large_chunked_response_stops_reading_and_closes_connection(monkeypatch):
    monkeypatch.setattr(provider_module, "MAX_RESPONSE_BYTES", 65536)
    stream = CountingStream([b"x" * 65536] * 4)
    async with Provider(config(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream))) as client:
        with pytest.raises(ProviderError, match="响应过大"):
            await client.generate(Reply, "系统", "正文")
    assert stream.reads == 2 and stream.closed


@pytest.mark.parametrize("headers,match", [
    ({"Content-Length": str(provider_module.MAX_RESPONSE_BYTES + 1)}, "响应过大"),
    ({"Content-Length": "not-a-length"}, "响应长度"),
    ({"Content-Encoding": "gzip"}, "压缩响应"),
    ({"Content-Encoding": "br"}, "压缩响应"),
])
async def test_unacceptable_response_headers_abort_before_body_read(headers, match):
    stream = CountingStream([b"private provider data"])
    async with Provider(config(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers=headers, stream=stream))) as client:
        with pytest.raises(ProviderError, match=match):
            await client.generate(Reply, "系统", "正文")
    assert not stream.reads and stream.closed


@pytest.mark.parametrize("status", [302, 401, 500])
async def test_error_bodies_are_never_read(status):
    stream = CountingStream([b"fake-test-key"] * 100)
    async with Provider(config(), transport=httpx.MockTransport(
            lambda request: httpx.Response(status, stream=stream))) as client:
        with pytest.raises(ProviderError) as caught:
            await client.generate(Reply, "系统", "正文")
    assert not stream.reads and stream.closed
    assert "fake-test-key" not in "".join(traceback.format_exception(caught.value))


async def test_total_deadline_also_stops_slow_generation_body(monkeypatch):
    timeouts = []
    original_timeout = provider_module.asyncio.timeout

    def short_timeout(delay):
        timeouts.append(delay)
        return original_timeout(.01)

    class SlowStream(CountingStream):
        async def __aiter__(self):
            yield b"{"
            await provider_module.asyncio.sleep(60)
            yield b"}"

    stream = SlowStream([])
    monkeypatch.setattr(provider_module.asyncio, "timeout", short_timeout)
    async with Provider(config(timeout_seconds=10), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream))) as client:
        with pytest.raises(ProviderError, match="模型响应超时"):
            await client.generate(Reply, "系统", "正文")
    assert timeouts == [10] and stream.closed


async def test_only_known_finite_nonnegative_usage_counters_are_saved():
    body = envelope()
    body["usage"] = {"fake-test-key": 123, "unknown_counter": 7, "total_tokens": float("inf"),
                     "input_tokens": -3, "completion_tokens": True, "prompt_tokens": 12,
                     "output_tokens": 10**200, "prompt_cache_hit_tokens": 3}
    async with Provider(config(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(body)))) as client:
        await client.generate(Reply, "系统", "正文")
        assert client.usage == [{"prompt_tokens": 12, "prompt_cache_hit_tokens": 3}]


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_exception_traceback_does_not_echo_url_or_key(error):
    def fail(request):
        raise error("private-source fake-test-key", request=request)

    async with Provider(config(), transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(ProviderError) as caught:
            await client.generate(Reply, "系统", "正文")
    rendered = "".join(traceback.format_exception(caught.value))
    assert "private-source" not in rendered and "fake-test-key" not in rendered


@pytest.mark.parametrize("url,trust_env", [("http://localhost:11434/v1", False),
                                            ("https://model.example/v1", True)])
async def test_local_plaintext_api_ignores_environment_proxy(url, trust_env, monkeypatch):
    captured = []
    original_client = httpx.AsyncClient

    def capture_client(**kwargs):
        captured.append(kwargs)
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", capture_client)
    async with Provider(APIConfig(base_url=url), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=envelope()))) as client:
        await client.generate(Reply, "系统", "正文")
    assert captured[0]["trust_env"] is trust_env
    assert captured[0]["follow_redirects"] is False
    assert captured[0]["headers"]["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("settings", [
    APIConfig(base_url="https://fake-secret@model.example/v1"),
    APIConfig(base_url="http://remote.example/v1"),
    APIConfig(api_key=SecretStr("fake-secret\r\nInjected: yes")),
])
def test_direct_provider_construction_cannot_bypass_config_validation(settings):
    with pytest.raises(ValueError) as caught:
        Provider(settings)
    assert "fake-secret" not in str(caught.value)


async def test_excessively_nested_json_has_sanitized_error():
    raw = b'{"private-field":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
    async with Provider(config(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=raw))) as client:
        with pytest.raises(ProviderError, match="JSON") as caught:
            await client.generate(Reply, "系统", "正文")
    assert "private-field" not in "".join(traceback.format_exception(caught.value))


async def test_nested_model_output_repair_has_no_raw_values():
    text = '{"answer":' + "[" * 130 + '"fake-test-key"' + "]" * 130 + "}"
    sent = []

    def respond(request):
        sent.append(request.content)
        return httpx.Response(200, json=envelope(text=text))

    async with Provider(config(), transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match="嵌套层级") as caught:
            await client.generate(Reply, "系统", "正文")
    assert len(sent) == 2 and b"fake-test-key" not in sent[1]
    assert "fake-test-key" not in "".join(traceback.format_exception(caught.value))


def test_json_depth_check_ignores_brackets_inside_escaped_strings():
    answer = '[\\"{}]' * 500
    assert decode_json(json.dumps({"answer": answer})) == {"answer": answer}


async def test_image_data_limit_is_applied_while_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_module, "MAX_IMAGE_BYTES", 32)
    picture = tmp_path / "oversized.png"
    picture.write_bytes(b"x" * 100)

    def no_request(_):
        raise AssertionError("Oversized image must not be sent")

    async with Provider(config(), transport=httpx.MockTransport(no_request)) as client:
        with pytest.raises(ProviderError, match="图片数据过大"):
            await client.generate(Reply, "系统", "正文", [picture])


def test_malformed_environment_proxy_does_not_echo_its_credentials(monkeypatch):
    # Construct the real HTTPX client; no request is sent.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "ftp://fake-proxy-secret@proxy.example:3128")
    with pytest.raises(ProviderError, match="代理与证书") as caught:
        Provider(config())
    rendered = "".join(traceback.format_exception(caught.value))
    assert "fake-proxy-secret" not in rendered and "fake-test-key" not in rendered


@pytest.mark.parametrize("error", [ValueError, OSError, httpx.ProxyError, httpx.InvalidURL])
def test_client_setup_errors_have_sanitized_tracebacks(error, monkeypatch):
    def fail_client(**_):
        raise error("fake-proxy-secret private-certificate-path")

    monkeypatch.setattr(httpx, "AsyncClient", fail_client)
    with pytest.raises(ProviderError, match="代理与证书") as caught:
        Provider(config())
    rendered = "".join(traceback.format_exception(caught.value))
    assert "fake-proxy-secret" not in rendered and "private-certificate-path" not in rendered
