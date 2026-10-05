from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from learnmargin import extraction_worker as worker
from learnmargin.extraction_worker import ExtractionError, extract_in_worker


async def test_real_worker_extracts_small_text_without_retaining_result_files(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("理解条件概率，再观察分母。", encoding="utf-8")
    output = tmp_path / "output"
    document = await extract_in_worker(source, output, "sample", "课程.txt")
    assert document.id == "sample" and document.name == "课程.txt"
    assert "条件概率" in document.units[0].text
    assert list(output.iterdir()) == []


async def test_real_worker_reports_bad_file_without_native_error(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"fake-secret not a PDF")
    with pytest.raises(ExtractionError, match="无法读取") as caught:
        await extract_in_worker(source, tmp_path / "output", "sample", "课程.pdf")
    assert "fake-secret" not in str(caught.value)


def test_worker_environment_is_an_allowlist_and_keeps_office_opt_in(monkeypatch):
    monkeypatch.setenv("LEARNMARGIN_API_KEY", "fake-key")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("CUSTOM_MODEL_SECRET", "fake-key")
    monkeypatch.setenv("HTTPS_PROXY", "https://fake-credential@proxy.example")
    monkeypatch.setenv("PYTHONPATH", "untrusted-python-directory")
    monkeypatch.setenv("LEARNMARGIN_ALLOW_LOCAL_OFFICE", "1")
    monkeypatch.setenv("LEARNMARGIN_OFFICE_WSL_DISTRIBUTION", "LearnMargin-Office")
    result = worker.worker_environment()
    assert "fake-key" not in json.dumps(result)
    assert "HTTPS_PROXY" not in result and "PYTHONPATH" not in result
    assert result["LEARNMARGIN_ALLOW_LOCAL_OFFICE"] == "1"
    assert result["LEARNMARGIN_OFFICE_WSL_DISTRIBUTION"] == "LearnMargin-Office"
    assert result["LEARNMARGIN_EXTRACTION_WORKER"] == "1"


def test_unix_limit_failure_returns_error_before_importing_document_parsers(monkeypatch):
    response = io.BytesIO()

    def fail_limits():
        raise OSError("private system detail fake-secret")

    monkeypatch.setattr(worker, "WINDOWS", False)
    monkeypatch.setattr(worker, "_unix_limits", fail_limits)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"{}\n")))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=response))
    assert worker._worker_main() == 1
    message = json.loads(response.getvalue())
    assert message == {"ok": False, "error": worker.LIMIT_ERROR}


class FakeStdin:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    pid = 1234
    returncode = None

    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = asyncio.StreamReader()

    async def wait(self):
        return 0


class FakeLimiter:
    def __init__(self, process, fail=False):
        self.process = process
        self.fail = fail
        self.closed = False

    def attach(self, pid):
        assert pid == self.process.pid
        assert not self.process.stdin.written, "No material may be read before attachment"
        if self.fail:
            raise ExtractionError(worker.LIMIT_ERROR)

    def close(self):
        self.closed = True


def fake_worker(monkeypatch, *, fail_attach=False):
    process = FakeProcess()
    limiter = FakeLimiter(process, fail_attach)
    stopped = []

    async def spawn():
        return process

    async def stop(actual, actual_limiter):
        assert actual is process and actual_limiter is limiter
        actual_limiter.close()
        stopped.append(actual)

    monkeypatch.setattr(worker, "_spawn_worker", spawn)
    monkeypatch.setattr(worker, "_create_limiter", lambda: limiter)
    monkeypatch.setattr(worker, "_terminate_worker", stop)
    return process, limiter, stopped


async def test_attach_failure_never_releases_gate_and_reaps_child(tmp_path, monkeypatch):
    process, limiter, stopped = fake_worker(monkeypatch, fail_attach=True)
    with pytest.raises(ExtractionError, match="资源限制"):
        await extract_in_worker(tmp_path / "source.txt", tmp_path / "out", "sample")
    assert not process.stdin.written and limiter.closed and stopped == [process]


async def test_timeout_stops_owned_worker(tmp_path, monkeypatch):
    process, limiter, stopped = fake_worker(monkeypatch)
    monkeypatch.setattr(worker, "WALL_SECONDS", .01)
    with pytest.raises(ExtractionError, match="已停止"):
        await extract_in_worker(tmp_path / "source.txt", tmp_path / "out", "sample")
    assert len(process.stdin.written) == 1 and limiter.closed and stopped == [process]


async def test_cancellation_stops_owned_worker(tmp_path, monkeypatch):
    process, limiter, stopped = fake_worker(monkeypatch)
    task = asyncio.create_task(extract_in_worker(tmp_path / "source.txt", tmp_path / "out", "sample"))
    for _ in range(10):
        await asyncio.sleep(0)
        if process.stdin.written:
            break
    assert process.stdin.written
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limiter.closed and stopped == [process]


async def test_repeated_cancellation_waits_for_owned_cleanup(tmp_path, monkeypatch):
    process, limiter, _ = fake_worker(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def cleanup(actual, actual_limiter):
        assert actual is process and actual_limiter is limiter
        entered.set()
        await release.wait()
        limiter.close()

    monkeypatch.setattr(worker, "_terminate_worker", cleanup)
    task = asyncio.create_task(extract_in_worker(tmp_path / "source.txt", tmp_path / "out", "sample"))
    while not process.stdin.written:
        await asyncio.sleep(0)
    task.cancel()
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and not limiter.closed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limiter.closed


async def test_cancellation_while_spawning_does_not_lose_child(tmp_path, monkeypatch):
    process, limiter, stopped = fake_worker(monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def spawn():
        entered.set()
        await release.wait()
        return process

    monkeypatch.setattr(worker, "_spawn_worker", spawn)
    task = asyncio.create_task(extract_in_worker(tmp_path / "source.txt", tmp_path / "out", "sample"))
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limiter.closed and stopped == [process]
    assert not process.stdin.written


async def test_oversized_worker_result_is_not_retained(tmp_path, monkeypatch):
    process, limiter, stopped = fake_worker(monkeypatch)
    monkeypatch.setattr(worker, "MAX_RESULT_BYTES", 32)
    process.stdout.feed_data(b"x" * 33)
    with pytest.raises(ExtractionError, match="资源限制"):
        await extract_in_worker(tmp_path / "source.txt", tmp_path / "out", "sample")
    assert limiter.closed and stopped == [process]


@pytest.mark.parametrize("body", [b"native crash fake-secret", b"[]", b'{"ok":true,"document":{}}'])
async def test_malformed_worker_result_does_not_echo_payload(tmp_path, monkeypatch, body):
    process, _, stopped = fake_worker(monkeypatch)
    process.stdout.feed_data(body)
    process.stdout.feed_eof()
    with pytest.raises(ExtractionError) as caught:
        await extract_in_worker(tmp_path / "source.txt", tmp_path / "out", "sample")
    assert "fake-secret" not in str(caught.value) and stopped == [process]


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object implementation")
async def test_real_windows_job_denies_allocation_over_its_small_budget():
    # The requested block is denied by Windows before it can consume that memory.
    code = ("import sys; sys.stdin.buffer.read(1)\n"
            "try:\n data=bytearray(128*1024*1024)\n print('allocated',flush=True)\n"
            "except MemoryError:\n print('limited',flush=True)\n")
    job = worker._WindowsJob(memory_limit=64 * 1024**2)
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-u", "-c", code, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, env=worker.worker_environment(),
        )
        job.attach(process.pid)
        stdout, _ = await asyncio.wait_for(process.communicate(b"1"), timeout=15)
        assert process.returncode == 0 and stdout.strip() == b"limited"
    finally:
        job.close()
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object implementation")
async def test_closing_windows_job_terminates_its_descendant_too():
    import ctypes
    from ctypes import wintypes

    code = ("import subprocess,sys,time; sys.stdin.buffer.read(1); "
            "p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'],"
            "creationflags=subprocess.CREATE_NO_WINDOW,stdin=subprocess.DEVNULL,"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            "print(p.pid,flush=True); time.sleep(60)")
    job = worker._WindowsJob(memory_limit=128 * 1024**2)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    process = descendant = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-u", "-c", code, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, env=worker.worker_environment(),
        )
        job.attach(process.pid)
        process.stdin.write(b"1")
        await process.stdin.drain()
        pid_line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        descendant = kernel.OpenProcess(0x100000, False, int(pid_line))  # SYNCHRONIZE only
        assert descendant
        job.close()
        await asyncio.wait_for(process.wait(), timeout=10)
        assert kernel.WaitForSingleObject(descendant, 1000) == 0  # WAIT_OBJECT_0: exited
    finally:
        job.close()
        if descendant:
            kernel.CloseHandle(descendant)
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
