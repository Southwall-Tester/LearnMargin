from __future__ import annotations

import base64
import json

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
