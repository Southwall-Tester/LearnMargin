import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from learnmargin.app import create_app
from learnmargin.models import APIConfig
from learnmargin.provider import Provider, ProviderError


def response_body(protocol="chat_completions", text="OK"):
    if protocol == "responses":
        return {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": text}]}]}
    return {"choices": [{"finish_reason": "stop", "message": {"content": text}}]}


def config(**overrides):
    return APIConfig(**{"base_url": "https://model.example/v1", "model": "test-model",
                        "api_key": "fake-connection-key", **overrides})


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
@pytest.mark.parametrize("json_mode", [True, False])
async def test_connection_probes_selected_protocol_once_with_small_fixed_payload(protocol, json_mode):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=response_body(protocol))

    settings = config(protocol=protocol, json_mode=json_mode, timeout_seconds=600)
    async with Provider(settings, transport=httpx.MockTransport(respond)) as provider:
        result = await provider.test_connection()
    assert result.ok and result.message == "连接成功" and result.model == "test-model"
    assert isinstance(result.latency_ms, int) and result.latency_ms >= 0
    assert len(requests) == 1
    request = requests[0]
    payload = json.loads(request.content)
    assert payload["model"] == "test-model"
    assert max(request.extensions["timeout"].values()) <= 30
    if protocol == "responses":
        assert request.url.path == "/v1/responses"
        assert payload["max_output_tokens"] == 256 and payload["store"] is False
        assert ("text" in payload) is json_mode
        assert len(payload["input"]) == 1 and len(payload["input"][0]["content"]) == 1
        assert payload["input"][0]["content"][0]["type"] == "input_text"
    else:
        assert request.url.path == "/v1/chat/completions"
        assert payload["max_tokens"] == 256
        assert ("response_format" in payload) is json_mode
        assert len(payload["messages"]) == 2
        assert all(isinstance(message["content"], str) for message in payload["messages"])
    assert len(request.content) < 500


@pytest.mark.parametrize("status,detail", [
    (401, "鉴权"), (403, "鉴权"), (402, "额度"), (429, "额度"),
    (400, "服务地址"), (404, "模型名"), (422, "JSON 模式"), (503, "HTTP 503"), (302, "HTTP 302"),
])
async def test_connection_errors_never_retry_or_expose_upstream_text(status, detail):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "fake-connection-key private provider detail"},
                              headers={"Location": "https://other.example/collect"})

    async with Provider(config(), transport=httpx.MockTransport(respond)) as provider:
        with pytest.raises(ProviderError, match=detail) as caught:
            await provider.test_connection()
    assert len(requests) == 1
    assert "fake-connection-key" not in str(caught.value) and "private" not in str(caught.value)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
@pytest.mark.parametrize("text", ["", " \n ", None, ["not a string"]])
async def test_connection_rejects_empty_or_nontext_success_envelopes(protocol, text):
    async with Provider(config(protocol=protocol), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response_body(protocol, text)))) as provider:
        with pytest.raises(ProviderError):
            await provider.test_connection()


@pytest.mark.parametrize("body", [[], {}, {"error": "private error details"}])
async def test_connection_rejects_malformed_success_envelopes(body):
    async with Provider(config(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body))) as provider:
        with pytest.raises(ProviderError) as caught:
            await provider.test_connection()
    assert "private" not in str(caught.value)


async def test_connection_rejects_html_success_response():
    async with Provider(config(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>private gateway page</html>"))) as provider:
        with pytest.raises(ProviderError, match="JSON") as caught:
            await provider.test_connection()
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
async def test_connection_rejects_truncated_text_even_when_nonempty(protocol):
    body = response_body(protocol)
    if protocol == "responses":
        body["status"] = "incomplete"
    else:
        body["choices"][0]["finish_reason"] = "length"
    async with Provider(config(protocol=protocol), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body))) as provider:
        with pytest.raises(ProviderError, match="未完整|截断"):
            await provider.test_connection()


@pytest.mark.parametrize("error,detail", [(httpx.ReadTimeout, "超时"), (httpx.ConnectError, "网络")])
async def test_connection_reports_transport_errors_without_retries(error, detail):
    requests = []

    def respond(request):
        requests.append(request)
        raise error("private URL and fake-connection-key", request=request)

    async with Provider(config(), transport=httpx.MockTransport(respond)) as provider:
        with pytest.raises(ProviderError, match=detail) as caught:
            await provider.test_connection()
    assert len(requests) == 1 and "private" not in str(caught.value)


async def test_connection_total_timeout_cancels_slow_request(monkeypatch):
    timeouts = []
    timeout = asyncio.timeout

    def short_timeout(delay):
        timeouts.append(delay)
        return timeout(.01)

    async def respond(request):
        await asyncio.sleep(60)
        return httpx.Response(200, json=response_body())

    monkeypatch.setattr("learnmargin.provider.asyncio.timeout", short_timeout)
    async with Provider(config(timeout_seconds=10), transport=httpx.MockTransport(respond)) as provider:
        with pytest.raises(ProviderError, match="连接测试超时"):
            await provider.test_connection()
    assert timeouts == [10]


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in ("LEARNMARGIN_API_KEY", "LEARNMARGIN_BASE_URL", "LEARNMARGIN_MODEL", "LEARNMARGIN_PROTOCOL",
                 "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(tmp_path)) as value:
        yield value


def mock_api(monkeypatch, requests, *, status=200):
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=response_body())

    monkeypatch.setattr("learnmargin.app.Provider",
                        lambda settings: Provider(settings, transport=httpx.MockTransport(respond)))


def test_connection_route_does_not_read_materials_or_save_keys_config_or_jobs(client, monkeypatch):
    requests = []
    mock_api(monkeypatch, requests)
    store = client.app.state.store
    files_before = {path.relative_to(store.root): path.read_bytes()
                    for path in store.root.rglob("*") if path.is_file()}

    def reject_document(*_):
        raise AssertionError("Connection testing must not read uploaded documents")

    monkeypatch.setattr(store, "document", reject_document)
    response = client.post("/api/connection-test", json={"api_key": "fake-connection-key"})
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "message": "连接成功", "model": "deepseek-flash",
                               "latency_ms": response.json()["latency_ms"]}
    assert "fake-connection-key" not in response.text and len(requests) == 1
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/jobs").json() == [] and not client.app.state.jobs.tasks
    assert files_before == {path.relative_to(store.root): path.read_bytes()
                            for path in store.root.rglob("*") if path.is_file()}


def test_connection_route_environment_key_stays_bound_to_endpoint(client, monkeypatch):
    requests = []
    mock_api(monkeypatch, requests)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-environment-key")
    assert client.post("/api/connection-test", json={}).status_code == 200
    assert requests[0].headers["authorization"] == "Bearer fake-environment-key"
    response = client.post("/api/connection-test", json={"base_url": "https://other.example/v1"})
    assert response.status_code == 400 and "密钥" in response.json()["detail"]
    assert "fake-environment-key" not in response.text and len(requests) == 1


def test_connection_route_accepts_local_service_without_empty_authorization_header(client, monkeypatch):
    requests = []
    mock_api(monkeypatch, requests)
    response = client.post("/api/connection-test", json={"base_url": "http://localhost:11434/v1"})
    assert response.status_code == 200 and "authorization" not in requests[0].headers


@pytest.mark.parametrize("body,status", [
    ({}, 400), ({"model": " "}, 400), ({"base_url": "http://model.example"}, 400),
    ({"protocol": "invalid", "api_key": "fake-secret"}, 422),
])
def test_connection_route_validates_before_outbound_request(client, monkeypatch, body, status):
    requests = []
    mock_api(monkeypatch, requests)
    response = client.post("/api/connection-test", json=body)
    assert response.status_code == status and isinstance(response.json()["detail"], str)
    assert "fake-secret" not in response.text and not requests


def test_connection_route_protects_origin_and_returns_standard_provider_error(client, monkeypatch):
    requests = []
    mock_api(monkeypatch, requests, status=401)
    body = {"api_key": "fake-connection-key"}
    assert client.post("/api/connection-test", json=body,
                       headers={"Origin": "https://other.example"}).status_code == 403
    assert not requests
    response = client.post("/api/connection-test", json=body)
    assert response.status_code == 400 and "鉴权" in response.json()["detail"]
    assert "fake-connection-key" not in response.text
