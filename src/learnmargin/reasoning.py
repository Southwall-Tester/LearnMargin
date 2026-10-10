"""Documented native controls, matched by endpoint, protocol and exact model ID.

See docs/REASONING.md for sources and extension rules. Unknown gateways/models
remain usable with provider defaults; never guess controls from a model prefix.
"""
from .models import APIConfig

_LABELS = {
    "none": "关闭（none）", "minimal": "最少（minimal）", "low": "低（low）",
    "medium": "中（medium）", "high": "高（high）", "xhigh": "更高（xhigh）",
    "max": "最高（max）", "enabled": "开启", "disabled": "关闭",
}
_DEEPSEEK = ["https://api.deepseek.com", "https://api.deepseek.com/v1"]
_ZAI = ["https://api.z.ai/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4"]
_OPENAI = ["https://api.openai.com/v1"]


def _profile(endpoints, models, protocols, efforts, adapter, note=""):
    return {"endpoints": endpoints, "models": models, "protocols": protocols,
            "options": [{"value": effort, "label": _LABELS[effort]} for effort in efforts],
            "adapter": adapter, "note": note}


PROFILES = [
    _profile(_DEEPSEEK, ["deepseek-flash", "deepseek-v4-pro"],
             ["chat_completions", "responses"], ["none", "low", "high", "max"], "deepseek"),
    _profile(_ZAI, ["glm-5.3", "glm-5.3-flash", "glm-5.3-flashx"],
             ["chat_completions"], ["low", "high", "max"], "effort", "此模型必须开启思考。"),
    _profile(_ZAI, ["glm-4.7"], ["chat_completions"], ["enabled", "disabled"], "toggle"),
    _profile(_OPENAI, ["gpt-6-astra", "gpt-6.1-sol"], ["chat_completions", "responses"],
             ["low", "medium", "high", "xhigh", "max"], "effort", "此模型不支持关闭思考。"),
    _profile(_OPENAI, ["gpt-6-sol", "gpt-6-luna"], ["chat_completions", "responses"],
             ["none", "low", "medium", "high", "xhigh", "max"], "effort"),
    _profile(_OPENAI, ["gpt-5", "gpt-5-mini", "gpt-5-nano"],
             ["chat_completions", "responses"], ["minimal", "low", "medium", "high"], "effort"),
    _profile(_OPENAI, ["gpt-5.1"], ["chat_completions", "responses"],
             ["none", "low", "medium", "high"], "effort"),
    _profile(_OPENAI, ["gpt-5.2", "gpt-5.2-2025-12-11"], ["chat_completions", "responses"],
             ["none", "low", "medium", "high", "xhigh"], "effort"),
]


def public_reasoning_profiles() -> list[dict]:
    """The browser consumes the same capability table as request validation."""
    return [{key: value for key, value in profile.items() if key != "adapter"} for profile in PROFILES]


def reasoning_profile(config: APIConfig) -> dict | None:
    # The caller normalizes the URL and trims the model before matching.
    return next((profile for profile in PROFILES
                 if config.base_url in profile["endpoints"]
                 and config.model in profile["models"]
                 and config.protocol in profile["protocols"]), None)


def reasoning_parameters(config: APIConfig) -> dict:
    effort = config.reasoning_effort
    if effort is None:
        return {}
    profile = reasoning_profile(config)
    if profile is None:
        raise ValueError("此服务地址、协议和模型组合尚未适配思考设置，请选择“不指定”后重试。")
    if effort not in {option["value"] for option in profile["options"]}:
        raise ValueError("此模型不支持所选思考设置，请按当前模型的可用选项重新选择。")
    if profile["adapter"] == "toggle":
        return {"thinking": {"type": effort}}
    if config.protocol == "responses":
        return {"reasoning": {"effort": effort}}
    if profile["adapter"] == "deepseek":
        if effort == "none":
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
    return {"reasoning_effort": effort}
