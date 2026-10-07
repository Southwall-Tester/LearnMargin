"""Two configurable API protocols with bounded retries and validated JSON output."""
from __future__ import annotations

import asyncio
import base64
import json
import math
from pathlib import Path
from time import perf_counter
from typing import TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from .config import validate_api_config
from .models import APIConfig, ConnectionTestResult

T = TypeVar("T", bound=BaseModel)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 22 * 1024 * 1024
USAGE_FIELDS = frozenset({
    "input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
    "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
})


class ProviderError(ValueError):
    pass


class JSONDepthError(ValueError):
    pass


def bounded_json_loads(text: str) -> object:
    """Bound nesting before the parser allocates a deeply recursive structure."""
    depth = 0
    in_string = escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > 128:
                raise JSONDepthError("JSON 嵌套层级过深。")
        elif char in "]}":
            depth -= 1
    return json.loads(text)


def decode_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    value = bounded_json_loads(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def output_error_summary(error: ValueError | RecursionError, schema: type[BaseModel]) -> str:
    """Describe output faults without echoing model values or arbitrary field names."""
    if isinstance(error, json.JSONDecodeError):
        category = "invalid_escape" if error.msg.startswith("Invalid \\escape") else "invalid_json"
        return f"JSON 语法错误（{category}），第 {error.lineno} 行、第 {error.colno} 列"
    if isinstance(error, (RecursionError, JSONDepthError)):
        return "JSON 嵌套层级过深（too_deep）"
    if not isinstance(error, ValidationError):
        return "JSON 顶层必须是对象（object_required）"

    fields: set[str] = set()

    def collect_fields(node):
        if isinstance(node, dict):
            fields.update(node.get("properties", {}))
            for value in node.values():
                collect_fields(value)
        elif isinstance(node, list):
            for value in node:
                collect_fields(value)

    collect_fields(schema.model_json_schema())
    error_types = {
        "missing": "缺少必填字段",
        "extra_forbidden": "存在未定义字段",
        "string_type": "应为字符串",
        "string_too_short": "字符串长度不足",
        "string_too_long": "字符串超出长度限制",
        "list_type": "应为数组",
        "dict_type": "应为对象",
        "model_type": "应为对象",
        "int_type": "应为整数",
        "int_parsing": "应为整数",
        "literal_error": "取值不在允许范围内",
        "too_short": "项目数量不足",
        "too_long": "项目数量超出限制",
        "greater_than_equal": "数值小于允许下限",
        "less_than_equal": "数值大于允许上限",
        "value_error": "未满足字段约束或字段间条件",
        "question_answer_required": "需要作答的侧栏提示必须填写非空参考答案 answer",
        "action_answer_not_allowed": "流程指导 action 的 answer 必须为 null；新增知识问题使用 question 并提供答案",
        "guidance_placement_without_practice": "没有 practice 时，侧栏 placement 不能选 before_practice 或 after_practice",
    }
    summaries = []
    for detail in error.errors(include_url=False, include_input=False, include_context=False)[:6]:
        location = "$"
        for part in detail["loc"][:8]:
            if isinstance(part, int):
                location += f"[{part}]"
            elif part in fields:
                location += f".{part}"
            else:
                location += ".[未定义字段]"
        kind = detail["type"] if detail["type"] in error_types else "value_error"
        summaries.append(f"{location}：{error_types[kind]}（{kind}）")
    return "；".join(summaries)


class Provider:
    def __init__(self, config: APIConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = validate_api_config(config)
        key = self.config.api_key.get_secret_value()
        try:
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds, connect=20),
                # Keep explicitly configured HTTPS proxies usable, but never send
                # local plaintext traffic through a proxy inherited from the shell.
                follow_redirects=False, trust_env=urlsplit(self.config.base_url).scheme == "https",
                transport=transport,
                # Request plain JSON so even hostile compressed bodies cannot expand
                # without a bound inside HTTPX's automatic content decoder.
                headers={"Accept-Encoding": "identity", **({"Authorization": f"Bearer {key}"} if key else {})},
            )
        except (ValueError, OSError, httpx.HTTPError, httpx.InvalidURL):
            # HTTPX may echo a malformed environment proxy URL (whose username
            # itself can be a credential), or a private certificate path.
            raise ProviderError("无法初始化 API 连接，请检查代理与证书配置。") from None
        self.usage: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def _request(self, payload: dict, *, retry_transient: bool = True,
                       timeout_seconds: float | None = None) -> dict:
        limit = self.config.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            # I/O timeouts alone can be kept alive by a byte-at-a-time response.
            # This deadline also covers retries, their delays, and body reading.
            async with asyncio.timeout(limit):
                return await self._request_with_retries(payload, retry_transient, limit)
        except (TimeoutError, httpx.TimeoutException):
            if timeout_seconds is not None:
                raise ProviderError("连接测试超时，请检查服务状态和网络连接后重试。") from None
            raise ProviderError("模型响应超时。请缩小学习范围或提高超时时间后重新生成。") from None
        except httpx.HTTPError:
            raise ProviderError("无法连接 API，请检查服务地址和网络连接。") from None

    async def _request_with_retries(self, payload: dict, retry_transient: bool, timeout_seconds: float) -> dict:
        endpoint = "/responses" if self.config.protocol == "responses" else "/chat/completions"
        attempts = 3 if retry_transient else 1
        for attempt in range(attempts):
            async with self.client.stream(
                "POST", self.config.base_url.rstrip("/") + endpoint, json=payload,
                timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 20)),
            ) as response:
                retry = response.status_code in {429, 502, 503, 504} and attempt < attempts - 1
                if not retry:
                    if response.status_code in {401, 403}:
                        raise ProviderError("API 鉴权失败，请核对该服务的密钥与模型权限。")
                    if response.status_code in {402, 429}:
                        raise ProviderError("API 请求额度或速率受限，请检查账户额度后稍后重试。")
                    if response.status_code in {400, 404, 405, 422}:
                        raise ProviderError(
                            f"API 返回 HTTP {response.status_code}。请核对服务地址、协议、模型名与 JSON 模式。"
                        )
                    if response.status_code >= 300:
                        raise ProviderError(
                            f"API 返回 HTTP {response.status_code}。请核对协议、模型名、视觉能力与 JSON 模式。"
                        )
                    body = await self._read_body(response)
            if retry:
                await asyncio.sleep(2 ** (attempt + 1))
                continue
            try:
                result = bounded_json_loads(body.decode("utf-8-sig"))
            except (ValueError, RecursionError):
                raise ProviderError("API 未返回有效 JSON 响应，请检查 Base URL。") from None
            if not isinstance(result, dict):
                raise ProviderError("API 响应结构不受支持。")
            usage = result.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            # Only documented counters are persisted; arbitrary upstream keys can
            # contain secrets even when their values are harmless numbers.
            self.usage.append({key: value for key, value in usage.items()
                               if key in USAGE_FIELDS and type(value) in {int, float}
                               and 0 <= value <= 2**63 - 1 and math.isfinite(value)})
            return result
        raise ProviderError("API 暂时不可用。")

    @staticmethod
    async def _read_body(response: httpx.Response) -> bytes:
        if response.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
            raise ProviderError("API 返回了未请求的压缩响应，请配置网关返回未压缩 JSON。")
        length = response.headers.get("content-length")
        if length is not None:
            try:
                announced = int(length)
            except ValueError:
                raise ProviderError("API 返回了无效的响应长度。") from None
            if announced < 0 or announced > MAX_RESPONSE_BYTES:
                raise ProviderError("API 响应过大，请缩小学习范围或检查服务配置。")
        if response.is_stream_consumed:
            # Already-buffered responses are used by embedded/mock transports.
            data = response.content
            if len(data) > MAX_RESPONSE_BYTES:
                raise ProviderError("API 响应过大，请缩小学习范围或检查服务配置。")
            return data
        data = bytearray()
        async for chunk in response.aiter_raw(chunk_size=65536):
            if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ProviderError("API 响应过大，请缩小学习范围或检查服务配置。")
            data.extend(chunk)
        return bytes(data)

    async def test_connection(self) -> ConnectionTestResult:
        """Send one bounded text-only probe; never retry or include learning material."""
        instruction = ('Return only this JSON object: {"ok":true}.' if self.config.json_mode
                       else "Reply only OK.")
        payload = self._payload("You are testing an API connection.", instruction, [])
        limit_key = "max_output_tokens" if self.config.protocol == "responses" else "max_tokens"
        payload[limit_key] = 256
        timeout_seconds = min(self.config.timeout_seconds, 30)
        started = perf_counter()
        result = await self._request(payload, retry_transient=False, timeout_seconds=timeout_seconds)
        if result.get("error") or not self._text(result).strip():
            raise ProviderError("API 未返回有效文本，连接测试未通过。请核对服务协议和模型名。")
        return ConnectionTestResult(model=self.config.model, latency_ms=round((perf_counter() - started) * 1000))

    def _payload(self, system: str, user: str, images: list[Path]) -> dict:
        encoded = []
        total = 0
        for image in images:
            with image.open("rb") as stream:
                data = stream.read(MAX_IMAGE_BYTES - total + 1)
            total += len(data)
            if total > MAX_IMAGE_BYTES:
                raise ProviderError("本轮图片数据过大，请缩小范围后再生成。")
            mime = "image/jpeg" if image.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            encoded.append(f"data:{mime};base64," + base64.b64encode(data).decode("ascii"))
        if self.config.protocol == "responses":
            content = [{"type": "input_text", "text": user}]
            content.extend({"type": "input_image", "image_url": url} for url in encoded)
            result = {"model": self.config.model, "instructions": system,
                      "input": [{"role": "user", "content": content}], "max_output_tokens": 12000,
                      "store": False}
            if self.config.json_mode:
                result["text"] = {"format": {"type": "json_object"}}
        else:
            content = [{"type": "text", "text": user}]
            content.extend({"type": "image_url", "image_url": {"url": url}} for url in encoded)
            result = {"model": self.config.model, "messages": [{"role": "system", "content": system},
                      {"role": "user", "content": content if encoded else user}], "max_tokens": 12000}
            if self.config.json_mode:
                result["response_format"] = {"type": "json_object"}
            if urlsplit(self.config.base_url).hostname == "api.deepseek.com":
                result["thinking"] = {"type": "disabled"}
        return result

    def _text(self, response: dict) -> str:
        if self.config.protocol == "responses":
            if response.get("status") in {"failed", "incomplete", "cancelled"}:
                raise ProviderError("模型未完整生成结果，请缩小范围或更换模型后重试。")
            output = response.get("output")
            if not isinstance(output, list):
                raise ProviderError("API 响应的 output 结构不受支持。")
            pieces = []
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    raise ProviderError("API 响应的 content 结构不受支持。")
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        if not isinstance(part.get("text"), str):
                            raise ProviderError("API 响应没有有效的文本内容。")
                        pieces.append(part["text"])
            return "\n".join(pieces)
        choices = response.get("choices") or []
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError("模型响应没有可用内容。")
        if choices[0].get("finish_reason") in {"length", "content_filter"}:
            raise ProviderError("模型输出被截断或未完成，请缩小范围后重试。")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ProviderError("API 响应的 message 结构不受支持。")
        content = message.get("content")
        return content if isinstance(content, str) else ""

    async def generate(self, schema: type[T], system: str, user: str,
                       images: list[Path] | None = None) -> T:
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        instructions = system + "\n只输出完整 JSON 对象，符合下面 JSON Schema；不要输出代码围栏：\n" + schema_text
        for attempt in range(2):
            result = await self._request(self._payload(instructions, user, images or []))
            text = self._text(result)
            try:
                return schema.model_validate(decode_json(text))
            except (ValueError, RecursionError) as error:
                summary = output_error_summary(error, schema)
                if attempt:
                    raise ProviderError(
                        f"{schema.__name__} 两次输出均未通过结构校验（已重试 1 次）：{summary}。"
                    ) from None
                user += (
                    f"\n上次输出未通过结构校验，具体问题：{summary}。"
                    "\n请对照 JSON Schema 和上述生成要求修正这些位置，重新生成完整 JSON 对象，"
                    "保留所需全部字段与材料引用；不要只输出补丁或错误解释。"
                    "JSON 字符串中的换行、引号和 LaTeX 反斜杠必须正确转义。"
                )
        raise ProviderError("模型输出不可用。")
