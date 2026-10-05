"""Local configuration. Credentials are never serialized into project data."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from platformdirs import user_data_path
from pydantic import SecretStr

from .models import APIConfig


def data_directory() -> Path:
    return Path(os.environ.get("LEARNMARGIN_DATA_DIR") or user_data_path("LearnMargin", appauthor=False))


def normalize_base_url(value: str) -> str:
    value = value.strip(" ")
    # urlsplit silently removes some control characters. Reject them before parsing
    # so validation and the HTTP client cannot disagree about the destination.
    if len(value) > 4096 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("API 地址过长或含有空白、控制字符，请检查地址。")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        # Parser errors may contain the supplied URL (including credentials).
        raise ValueError("API 地址或端口不正确。") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("API 地址需要完整的 http:// 或 https:// 地址。")
    if port == 0:
        raise ValueError("API 地址的端口不正确。") from None
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("API 地址不能包含账号、密钥、查询参数或片段；请把密钥填入独立字段。")
    if "\\" in value or "%" in parsed.netloc:
        raise ValueError("API 地址的主机名或路径格式不正确。")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程 API 请使用 HTTPS；HTTP 仅用于本机模型服务。")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    try:
        return str(httpx.URL(urlunsplit((parsed.scheme, parsed.netloc, path, "", "")))).rstrip("/")
    except (httpx.InvalidURL, ValueError):
        raise ValueError("API 地址的主机名或路径格式不正确。") from None


def validate_api_config(config: APIConfig) -> APIConfig:
    """Validate direct callers too, without looking up environment credentials."""
    result = config.model_copy(deep=True)
    result.base_url = normalize_base_url(result.base_url)
    result.model = result.model.strip()
    if not result.model:
        raise ValueError("请填写 API 模型名。")
    key = result.api_key.get_secret_value()
    if len(key) > 8192 or any(ord(char) < 32 or ord(char) >= 127 for char in key):
        raise ValueError("API 密钥格式不正确，请检查是否包含换行或非 ASCII 字符。")
    key = key.strip(" ")
    if " " in key:
        raise ValueError("API 密钥格式不正确，请检查是否包含空格。")
    result.api_key = SecretStr(key)
    return result


def default_api() -> APIConfig:
    return APIConfig(
        base_url=os.environ.get("LEARNMARGIN_BASE_URL", "https://api.deepseek.com"),
        model=os.environ.get("LEARNMARGIN_MODEL", "deepseek-flash"),
        protocol=os.environ.get("LEARNMARGIN_PROTOCOL", "chat_completions"),
    )


def resolve_api(config: APIConfig) -> APIConfig:
    result = validate_api_config(config)
    key = result.api_key.get_secret_value()
    # Environment credentials are bound to their configured endpoint, never forwarded
    # to a different provider merely because a user changed the URL field.
    if not key:
        configured_base = normalize_base_url(default_api().base_url)
        if result.base_url == configured_base:
            key = os.environ.get("LEARNMARGIN_API_KEY", "")
        if not key and result.base_url in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}:
            key = os.environ.get("DEEPSEEK_API_KEY", "")
        openai_base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if not key and result.base_url == normalize_base_url(openai_base):
            key = os.environ.get("OPENAI_API_KEY", "")
    result.api_key = SecretStr(key)
    return validate_api_config(result)


def skill_directory() -> Path:
    packaged = Path(__file__).parent / "resources" / "skill"
    if packaged.joinpath("SKILL.md").exists():
        return packaged
    development = Path(__file__).resolve().parents[2] / "skills" / "learnmargin"
    if development.joinpath("SKILL.md").exists():
        return development
    raise RuntimeError("缺少 LearnMargin skill 资源，请重新安装完整软件包。")
