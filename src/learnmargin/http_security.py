"""Bound local HTTP requests before JSON/multipart parsers consume their body."""
from __future__ import annotations

import asyncio
import contextlib
from ipaddress import ip_address
from tempfile import SpooledTemporaryFile
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .ingestion import MAX_UPLOAD_BYTES

MAX_JSON_BYTES = 256 * 1024
MAX_MULTIPART_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
BODY_TIMEOUT_SECONDS = 60


async def _spool_io(operation, *args):
    """Finish an in-flight file operation before cancellation closes its spool."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        with contextlib.suppress(Exception):
            task.result()
        raise asyncio.CancelledError
    return task.result()


def _same_origin(origin: str, scope: Scope, host: str) -> bool:
    if origin == "null" or any(ord(char) < 33 or ord(char) == 127 for char in origin):
        return False
    try:
        parsed = urlsplit(origin)
        return (parsed.scheme == scope.get("scheme", "http") and parsed.netloc == host
                and not parsed.path and not parsed.query and not parsed.fragment
                and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


class LocalRequestSecurity:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.active_uploads = 0
        self.active_tests = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        path, method = scope["path"], scope["method"]

        async def secured_send(message):
            if message["type"] == "http.response.start":
                outgoing = MutableHeaders(scope=message)
                outgoing["X-Content-Type-Options"] = "nosniff"
                outgoing["Referrer-Policy"] = "no-referrer"
                outgoing["X-Frame-Options"] = "SAMEORIGIN"
                outgoing["Cross-Origin-Resource-Policy"] = "same-origin"
                outgoing["Content-Security-Policy"] = (
                    "frame-ancestors 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"
                )
                if path.startswith("/api"):
                    outgoing["Cache-Control"] = "no-store"
            await send(message)

        async def reject(status: int, detail: str):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, secured_send)

        # This application has no multi-user authorization layer. Even when a
        # caller starts Uvicorn differently, a remote socket is not a local user.
        client = scope.get("client")
        if client and client[0] != "testclient":  # Starlette's in-process test transport
            try:
                local = ip_address(client[0]).is_loopback
            except ValueError:
                local = False
            if not local:
                await reject(403, "LearnMargin 仅接受本机连接。")
                return

        if path.startswith("/api"):
            origin = headers.get("origin")
            if (headers.get("sec-fetch-site") in {"cross-site", "same-site"}
                    or origin is not None and not _same_origin(origin, scope, headers.get("host", ""))):
                await reject(403, "请从 LearnMargin 本地页面操作。")
                return
        if method in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, secured_send)
            return

        upload = path == "/api/documents" and method == "POST"
        connection_test = path == "/api/connection-test" and method == "POST"
        counter = "active_uploads" if upload else "active_tests" if connection_test else None
        if counter and getattr(self, counter) >= 2:
            await reject(429, "当前导入或连接测试较多，请等待已有请求完成。")
            return
        limit = MAX_MULTIPART_BYTES if upload else MAX_JSON_BYTES
        declared = headers.get("content-length")
        if declared is not None:
            if not declared.isascii() or not declared.isdigit() or len(declared) > 16:
                await reject(400, "请求长度无效。")
                return
            if int(declared) > limit:
                await reject(413, "上传材料或请求内容过大，请缩小后重试。")
                return
        if headers.get("content-encoding", "identity").lower() != "identity":
            await reject(415, "不接受压缩的上传请求，请直接上传原文件。")
            return
        if counter:
            setattr(self, counter, getattr(self, counter) + 1)
        try:
            # Bound actual bytes even without Content-Length or with a forged
            # value. No multipart parser/file extraction runs until this passes.
            with SpooledTemporaryFile(max_size=1024 * 1024) as body:
                received = 0
                try:
                    async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
                        while True:
                            message = await receive()
                            if message["type"] == "http.disconnect":
                                return
                            chunk = message.get("body", b"")
                            received += len(chunk)
                            if received > limit:
                                await reject(413, "上传材料或请求内容过大，请缩小后重试。")
                                return
                            await _spool_io(body.write, chunk)
                            if not message.get("more_body", False):
                                break
                except TimeoutError:
                    await reject(408, "接收上传超时，请重新上传。")
                    return
                if declared is not None and received != int(declared):
                    await reject(400, "请求实际长度与声明不一致。")
                    return
                body.seek(0)
                complete = False

                async def bounded_receive():
                    nonlocal complete
                    if complete:
                        return await receive()
                    chunk = await _spool_io(body.read, 64 * 1024)
                    complete = body.tell() == received
                    return {"type": "http.request", "body": chunk, "more_body": not complete}

                await self.app(scope, bounded_receive, secured_send)
        finally:
            if counter:
                setattr(self, counter, getattr(self, counter) - 1)
