import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from learnmargin import http_security
from learnmargin.app import create_app
from learnmargin.http_security import LocalRequestSecurity


async def call_guard(guard, *, path="/api/jobs", headers=(), chunks=(b"{}",), client="127.0.0.1",
                     method="POST", receive_delay=0):
    messages = []
    sequence = iter(chunks)
    async def receive():
        await asyncio.sleep(receive_delay)
        chunk = next(sequence, None)
        return ({"type": "http.disconnect"} if chunk is None else
                {"type": "http.request", "body": chunk, "more_body": True})
    # Give the last actual chunk an explicit end marker, except where testing disconnects.
    position = 0
    async def complete_receive():
        nonlocal position
        if position >= len(chunks):
            return await receive()
        await asyncio.sleep(receive_delay)
        chunk = chunks[position]
        position += 1
        return {"type": "http.request", "body": chunk, "more_body": position < len(chunks)}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "scheme": "http",
             "path": path, "method": method, "client": (client, 1000),
             "headers": [(b"host", b"localhost"), *headers]}
    await guard(scope, complete_receive, send)
    return messages


def rejecting_downstream():
    calls = []
    async def downstream(scope, receive, send):
        calls.append(True)
        from starlette.responses import JSONResponse
        await JSONResponse({"ok": True})(scope, receive, send)
    return LocalRequestSecurity(downstream), calls


@pytest.mark.parametrize("headers,chunks", [
    ([(b"content-length", b"999999999")], (b"",)),
    ([], (b"a" * 70, b"b" * 70)),
    ([(b"content-length", b"10")], (b"a" * 70, b"b" * 70)),
])
async def test_actual_body_limit_runs_before_any_parser(monkeypatch, headers, chunks):
    monkeypatch.setattr(http_security, "MAX_JSON_BYTES", 100)
    guard, calls = rejecting_downstream()
    messages = await call_guard(guard, headers=headers, chunks=chunks)
    assert messages[0]["status"] == 413
    assert not calls


async def test_multipart_budget_applies_before_form_spooling(monkeypatch):
    monkeypatch.setattr(http_security, "MAX_MULTIPART_BYTES", 100)
    guard, calls = rejecting_downstream()
    messages = await call_guard(guard, path="/api/documents", chunks=(b"x"*80, b"x"*30))
    assert messages[0]["status"] == 413 and not calls
    assert guard.active_uploads == 0


@pytest.mark.parametrize("headers,status", [
    ([(b"content-length", b"1")], 400),
    ([(b"content-length", b"-1")], 400),
    ([(b"content-length", b"1,2")], 400),
    ([(b"content-encoding", b"gzip")], 415),
])
async def test_invalid_or_encoded_requests_do_not_reach_parser(headers, status):
    guard, calls = rejecting_downstream()
    messages = await call_guard(guard, headers=headers)
    assert messages[0]["status"] == status and not calls


async def test_slow_body_times_out_and_releases_admission(monkeypatch):
    monkeypatch.setattr(http_security, "BODY_TIMEOUT_SECONDS", .01)
    guard, calls = rejecting_downstream()
    messages = await call_guard(guard, path="/api/documents", receive_delay=.05)
    assert messages[0]["status"] == 408 and not calls
    assert guard.active_uploads == 0


async def test_cancelled_spool_operation_finishes_before_the_file_can_close():
    entered = threading.Event()
    release = threading.Event()
    def slow_write():
        entered.set()
        assert release.wait(3)
        return 1
    task = asyncio.create_task(http_security._spool_io(slow_write))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("headers", [
    [(b"origin", b"https://localhost")],
    [(b"origin", b"http://localhost/path")],
    [(b"origin", b"null")],
    [(b"origin", b"http://[")],
    [(b"origin", b"http://localhost#fragment")],
    [(b"sec-fetch-site", b"cross-site")],
    [(b"sec-fetch-site", b"same-site")],
])
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_cross_origin_and_malformed_origins_fail_closed(headers, method):
    guard, calls = rejecting_downstream()
    messages = await call_guard(guard, headers=headers, method=method)
    assert messages[0]["status"] == 403 and not calls


async def test_remote_socket_is_not_authorized_by_localhost_host_header():
    guard, calls = rejecting_downstream()
    messages = await call_guard(guard, client="192.0.2.1", method="GET")
    assert messages[0]["status"] == 403 and not calls


async def test_valid_body_is_replayed_without_truncation_and_gets_security_headers():
    received = []
    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            received.append(message["body"])
            if not message["more_body"]:
                break
        from starlette.responses import JSONResponse
        await JSONResponse({"ok": True})(scope, receive, send)
    original = b"a"*90_000 + b"b"*90_000
    messages = await call_guard(LocalRequestSecurity(downstream), chunks=(original[:90_000], original[90_000:]),
                                headers=[(b"origin", b"http://localhost"), (b"content-length", b"180000")])
    assert b"".join(received) == original
    headers = dict(messages[0]["headers"])
    assert headers[b"x-frame-options"] == b"SAMEORIGIN"
    assert headers[b"cross-origin-resource-policy"] == b"same-origin"
    assert b"frame-ancestors 'self'" in headers[b"content-security-policy"]
    assert headers[b"cache-control"] == b"no-store"


@pytest.mark.parametrize("path,counter", [("/api/documents", "active_uploads"),
                                          ("/api/connection-test", "active_tests")])
async def test_admission_is_bounded_and_cancellation_releases_slot(path, counter):
    entered = asyncio.Event()
    release = asyncio.Event()
    count = 0
    async def downstream(scope, receive, send):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
        await release.wait()
    guard = LocalRequestSecurity(downstream)
    first = asyncio.create_task(call_guard(guard, path=path))
    second = asyncio.create_task(call_guard(guard, path=path))
    await asyncio.wait_for(entered.wait(), 2)
    rejected = await call_guard(guard, path=path)
    assert rejected[0]["status"] == 429
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert getattr(guard, counter) == 1
    release.set()
    await second
    assert getattr(guard, counter) == 0


def test_upload_accepts_only_one_file_and_no_extra_fields(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/documents", files=[("file", ("a.txt", b"one")), ("file", ("b.txt", b"two"))])
        assert response.status_code == 400
        response = client.post("/api/documents", files={"file": ("a.txt", b"one")}, data={"extra": "value"})
        assert response.status_code == 400
        assert not list((tmp_path / "documents").iterdir())


def test_allowed_small_request_still_reaches_validation_without_secret_echo(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/jobs", json={"document_ids": [], "api": {"api_key": "private-test-key"}})
        assert response.status_code == 422
        assert "private-test-key" not in json.dumps(response.json())
        response = client.post("/api/jobs", json={"document_ids": [], "accidental-secret-as-field": 1})
        assert response.status_code == 422
        assert "accidental-secret-as-field" not in response.text


def test_deep_json_fails_as_a_safe_client_error(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/jobs", content=b"["*2000 + b"0" + b"]"*2000,
                               headers={"Content-Type": "application/json"})
        assert response.status_code in {400, 422}
        assert "Traceback" not in response.text


def test_upload_closes_form_file_when_storage_creation_fails(tmp_path, monkeypatch):
    closed = []
    from starlette.datastructures import UploadFile
    original = UploadFile.close
    async def close(file):
        closed.append(file.filename)
        await original(file)
    monkeypatch.setattr(UploadFile, "close", close)
    app = create_app(tmp_path)
    def fail(*_):
        raise ValueError("测试目录拒绝访问")
    monkeypatch.setattr(app.state.store, "directory", fail)
    with TestClient(app) as client:
        response = client.post("/api/documents", files={"file": ("a.txt", b"content")})
        assert response.status_code == 400
    assert closed == ["a.txt"]
