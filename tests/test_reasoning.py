import httpx
import pytest

from learnmargin.config import validate_api_config
from learnmargin.models import APIConfig
from learnmargin.provider import Provider
from learnmargin.reasoning import PROFILES, public_reasoning_profiles, reasoning_parameters


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
@pytest.mark.parametrize("effort", [None, "none", "low", "high", "max"])
async def test_deepseek_native_controls(protocol, effort):
    settings = APIConfig(base_url="https://API.DEEPSEEK.COM:443/v1/" +
                         ("responses" if protocol == "responses" else "chat/completions"),
                         protocol=protocol, reasoning_effort=effort)
    async with Provider(settings, transport=httpx.MockTransport(lambda _: None)) as provider:
        body = provider._payload("system", "user", [])
    controls = {k: v for k, v in body.items() if k in {"thinking", "reasoning", "reasoning_effort"}}
    if effort is None:
        assert controls == {}
    elif protocol == "responses":
        assert controls == {"reasoning": {"effort": effort}}
    elif effort == "none":
        assert controls == {"thinking": {"type": "disabled"}}
    else:
        assert controls == {"thinking": {"type": "enabled"}, "reasoning_effort": effort}


@pytest.mark.parametrize("endpoint", ["https://api.z.ai/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4"])
@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_glm_native_efforts(endpoint, effort):
    config = validate_api_config(APIConfig(base_url=endpoint, model="glm-5.3-flash", reasoning_effort=effort))
    assert reasoning_parameters(config) == {"reasoning_effort": effort}


@pytest.mark.parametrize("effort", ["enabled", "disabled"])
def test_toggle_only_model_has_no_fabricated_effort(effort):
    config = validate_api_config(APIConfig(base_url="https://api.z.ai/api/paas/v4",
                                           model="glm-4.7", reasoning_effort=effort))
    assert reasoning_parameters(config) == {"thinking": {"type": effort}}


@pytest.mark.parametrize("overrides", [
    {"reasoning_effort": "medium"},
    {"base_url": "https://gateway.example/v1"},
    {"base_url": "https://api.deepseek.com.evil.example"},
    {"base_url": "https://api.deepseek.com/custom"},
    {"model": "deepseek-flash-unknown"},
    {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-5.3-flash", "reasoning_effort": "none"},
    {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-5.3-flash", "protocol": "responses"},
    {"base_url": "https://api.z.ai/api/paas/v4", "model": "glm-4.7"},
    {"base_url": "https://api.openai.com/v1", "model": "gpt-5.2", "reasoning_effort": "max"},
])
def test_unsupported_controls_rejected_before_http_client(overrides, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("Invalid configuration must fail before constructing the HTTP client")
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    config = APIConfig(**({"reasoning_effort": "low"} | overrides))
    with pytest.raises(ValueError, match="思考"):
        Provider(config)
    config.reasoning_effort = None
    assert reasoning_parameters(validate_api_config(config)) == {}


def test_public_options_and_backend_validation_share_one_table():
    for public, profile in zip(public_reasoning_profiles(), PROFILES, strict=True):
        assert "adapter" not in public
        for endpoint in public["endpoints"]:
            for model in public["models"]:
                for protocol in public["protocols"]:
                    for option in public["options"]:
                        config = APIConfig(base_url=endpoint, model=model, protocol=protocol,
                                           reasoning_effort=option["value"])
                        assert reasoning_parameters(validate_api_config(config))
