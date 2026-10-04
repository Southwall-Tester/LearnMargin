"""Two configurable API protocols with bounded retries and validated JSON output."""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from time import perf_counter
from typing import TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from .models import APIConfig, ConnectionTestResult

T = TypeVar("T", bound=BaseModel)


class ProviderError(ValueError):
    pass


def decode_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def output_error_summary(error: ValueError, schema: type[BaseModel]) -> str:
    """Describe output faults without echoing model values or arbitrary field names."""
    if isinstance(error, json.JSONDecodeError):
        category = "invalid_escape" if error.msg.startswith("Invalid \\escape") else "invalid_json"
        return f"JSON 语法错误（{category}），第 {error.lineno} 行、第 {error.colno} 列"
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
        self.config = config
        key = config.api_key.get_secret_value()
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_seconds, connect=20),
            follow_redirects=False, transport=transport,
            headers={"Authorization": f"Bearer {key}"} if key else {},
        )
        self.usage: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def _request(self, payload: dict, *, retry_transient: bool = True,
                       timeout_seconds: float | None = None) -> dict:
        endpoint = "/responses" if self.config.protocol == "responses" else "/chat/completions"
        attempts = 3 if retry_transient else 1
        for attempt in range(attempts):
            try:
                options = {} if timeout_seconds is None else {"timeout": timeout_seconds}
                response = await self.client.post(
                    self.config.base_url.rstrip("/") + endpoint, json=payload, **options)
            except httpx.TimeoutException as exc:
                if timeout_seconds is not None:
                    raise ProviderError("连接测试超时，请检查服务状态和网络连接后重试。") from exc
                raise ProviderError("模型响应超时。请缩小学习范围或提高超时时间后重新生成。") from exc
            except httpx.HTTPError as exc:
                raise ProviderError("无法连接 API，请检查服务地址和网络连接。") from exc
            if response.status_code in {429, 502, 503, 504} and attempt < attempts - 1:
                await asyncio.sleep(2 ** (attempt + 1))
                continue
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
            try:
                result = response.json()
            except ValueError as exc:
                raise ProviderError("API 未返回有效 JSON 响应，请检查 Base URL。") from exc
            if not isinstance(result, dict):
                raise ProviderError("API 响应结构不受支持。")
            usage = result.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            self.usage.append({key: value for key, value in usage.items() if isinstance(value, (int, float))})
            return result
        raise ProviderError("API 暂时不可用。")

    async def test_connection(self) -> ConnectionTestResult:
        """Send one bounded text-only probe; never retry or include learning material."""
        instruction = ('Return only this JSON object: {"ok":true}.' if self.config.json_mode
                       else "Reply only OK.")
        payload = self._payload("You are testing an API connection.", instruction, [])
        limit_key = "max_output_tokens" if self.config.protocol == "responses" else "max_tokens"
        payload[limit_key] = 256
        timeout_seconds = min(self.config.timeout_seconds, 30)
        started = perf_counter()
        try:
            # httpx timeouts bound individual I/O phases; this also bounds total duration.
            async with asyncio.timeout(timeout_seconds):
                result = await self._request(payload, retry_transient=False, timeout_seconds=timeout_seconds)
        except TimeoutError as exc:
            raise ProviderError("连接测试超时，请检查服务状态和网络连接后重试。") from exc
        if result.get("error") or not self._text(result).strip():
            raise ProviderError("API 未返回有效文本，连接测试未通过。请核对服务协议和模型名。")
        return ConnectionTestResult(model=self.config.model, latency_ms=round((perf_counter() - started) * 1000))

    def _payload(self, system: str, user: str, images: list[Path]) -> dict:
        encoded = []
        total = 0
        for image in images:
            data = image.read_bytes()
            total += len(data)
            if total > 22 * 1024 * 1024:
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
            except ValueError as error:
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
