from __future__ import annotations

import pytest
from pydantic import SecretStr

from learnmargin.config import default_api, normalize_base_url, resolve_api
from learnmargin.models import APIConfig


@pytest.fixture(autouse=True)
def fake_credentials_only(monkeypatch):
    # Override every credential source with disposable values before exercising config.
    for name in ("LEARNMARGIN_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("LEARNMARGIN_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LEARNMARGIN_MODEL", "deepseek-flash")
    monkeypatch.setenv("LEARNMARGIN_PROTOCOL", "chat_completions")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")


@pytest.mark.parametrize("value,expected", [
    (" https://api.example/v1/ ", "https://api.example/v1"),
    ("https://api.example/v1/chat/completions", "https://api.example/v1"),
    ("https://api.example/v1/responses", "https://api.example/v1"),
    ("http://localhost:8000/v1", "http://localhost:8000/v1"),
    ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1"),
    ("http://[::1]:8000/v1", "http://[::1]:8000/v1"),
])
def test_normalize_supported_endpoint_forms(value, expected):
    assert normalize_base_url(value) == expected


@pytest.mark.parametrize("value", [
    "api.example/v1", "file:///tmp/model", "http://api.example/v1",
    "http://localhost.evil.example/v1", "https://user:password@api.example/v1",
    "https://api.example/v1?api_key=never-show", "https://api.example/v1#key",
    "https://api.example:invalid/v1", "https://api.example:99999/v1",
])
def test_invalid_or_credential_bearing_endpoints_rejected(value):
    with pytest.raises(ValueError) as caught:
        normalize_base_url(value)
    assert "never-show" not in str(caught.value)
    assert "password" not in str(caught.value)


def test_default_provider_and_model_are_deepseek():
    result = default_api()
    assert result.base_url == "https://api.deepseek.com"
    assert result.model == "deepseek-flash"
    assert result.protocol == "chat_completions"


def test_general_environment_key_only_used_for_its_configured_endpoint(monkeypatch):
    monkeypatch.setenv("LEARNMARGIN_BASE_URL", "https://gateway.example/v1/")
    monkeypatch.setenv("LEARNMARGIN_API_KEY", "fake-general-key")
    same = resolve_api(APIConfig(base_url="https://gateway.example/v1/chat/completions", model="test"))
    other = resolve_api(APIConfig(base_url="https://other.example/v1", model="test"))
    assert same.api_key.get_secret_value() == "fake-general-key"
    assert other.api_key.get_secret_value() == ""


def test_provider_specific_environment_keys_never_cross_endpoints(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    assert resolve_api(APIConfig()).api_key.get_secret_value() == "fake-deepseek-key"
    assert resolve_api(APIConfig(base_url="https://api.deepseek.com/v1")).api_key.get_secret_value() == "fake-deepseek-key"
    assert resolve_api(APIConfig(base_url="https://api.openai.com/v1")).api_key.get_secret_value() == "fake-openai-key"
    assert resolve_api(APIConfig(base_url="https://api.deepseek.com.evil.example")).api_key.get_secret_value() == ""
    assert resolve_api(APIConfig(base_url="http://localhost:8000/v1")).api_key.get_secret_value() == ""


def test_custom_openai_endpoint_is_explicit_binding(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://my-openai-gateway.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-gateway-key")
    custom = resolve_api(APIConfig(base_url="https://my-openai-gateway.example/v1"))
    default = resolve_api(APIConfig(base_url="https://api.openai.com/v1"))
    assert custom.api_key.get_secret_value() == "fake-gateway-key"
    assert default.api_key.get_secret_value() == ""


def test_explicit_key_wins_without_mutating_the_request(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-environment-key")
    original = APIConfig(api_key=SecretStr("  fake-explicit-key  "))
    result = resolve_api(original)
    assert result.api_key.get_secret_value() == "fake-explicit-key"
    assert original.api_key.get_secret_value() == "  fake-explicit-key  "
    assert "fake-explicit-key" not in result.model_dump_json()


def test_blank_model_rejected():
    with pytest.raises(ValueError, match="模型名"):
        resolve_api(APIConfig(model="  "))
