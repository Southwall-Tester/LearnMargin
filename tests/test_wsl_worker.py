"""Bounded wire protocol and real Linux parent-loss cleanup, with synthetic data."""
import json
import os
import selectors
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from learnmargin import linux_sandbox as sandbox
from learnmargin import wsl_worker as worker


def _request(**changes):
    value = {"version": 1, "suffix": ".rtf", "length": 3, "timeout": 20}
    value.update(changes)
    return json.dumps(value).encode()


@pytest.mark.parametrize("changes", [
    {"version": True}, {"version": 2}, {"suffix": "../../private"}, {"suffix": ".pdf"},
    {"length": 0}, {"length": -1}, {"length": True}, {"length": worker.MAX_BYTES + 1},
    {"timeout": float("nan")}, {"timeout": 0}, {"timeout": 151}, {"timeout": True},
    {"extra": "unexpected"},
])
def test_header_rejects_invalid_or_unbounded_requests(changes):
    with pytest.raises(worker.ProtocolError):
        worker._header(_request(**changes))


@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b"\xff", b"{", b'"text"'])
def test_header_rejects_non_object_or_invalid_json(raw):
    with pytest.raises(worker.ProtocolError):
        worker._header(raw)


def test_protocol_accepts_only_the_exact_supported_header():
    assert worker._header(_request()) == {"version": 1, "suffix": ".rtf", "length": 3, "timeout": 20}


def test_worker_refuses_root_before_reading_material(monkeypatch):
    monkeypatch.setattr(worker.sys, "platform", "linux")
    monkeypatch.setattr(worker.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(worker, "_read_exact", lambda *_: pytest.fail("material read as root"))
    assert worker.serve(sandbox) == 3


@pytest.fixture
def real_runtime():
    if sys.platform != "linux":
        pytest.skip("Real bridge pipes and namespaces need Linux")
    runtime = sandbox._runtime(None, require_office=False)
    try:
        command = sandbox._sandbox_command(runtime, [runtime.python, "-I", "-S", "-c", "print('ok')"])
        assert sandbox._run(command, 5, limit=64) == b"ok\n"
    except (sandbox.SandboxUnavailable, sandbox.SandboxConversionError):
        if os.environ.get("LEARNMARGIN_REQUIRE_SANDBOX_TESTS") == "1":
            pytest.fail("Required bubblewrap namespace support is unavailable")
        pytest.skip("bubblewrap namespaces unavailable")
    worker._tmpfs()
    return runtime


def _launch(tmp_path, mode):
    # Only the dedicated-distro marker/installed-LO checks are replaced. The
    # cancellation tests use the real bwrap namespace and real helper protocol.
    script = f"""
import os, stat
from pathlib import Path
from learnmargin import linux_sandbox as sandbox, wsl_worker as worker
worker._dedicated_environment = lambda: True
original_which = worker.shutil.which
worker.shutil.which = lambda name: '/usr/bin/unused-synthetic-converter' if name in ('libreoffice', 'soffice') else original_which(name)
original_temporary = worker.tempfile.TemporaryDirectory
def temporary(*args, **kwargs):
    result = original_temporary(*args, **kwargs)
    Path({str(tmp_path / 'task-path')!r}).write_text(result.name)
    return result
worker.tempfile.TemporaryDirectory = temporary
def convert(executable, source, output, *, timeout, cancelled):
    assert source.read_bytes() == b'rtf'
    assert stat.S_IMODE(source.stat().st_mode) == 0o400
    assert stat.S_IMODE(source.parent.stat().st_mode) == 0o700
    assert source.parent.parent == Path('/dev/shm')
    if {mode!r} in ('success', 'backpressure'):
        output.write_bytes(b'%PDF-synthetic-complete' + (b'x' * 1024**2 if {mode!r} == 'backpressure' else b''))
        return
    if {mode!r} == 'failure':
        raise sandbox.SandboxConversionError('fixed')
    runtime = sandbox._runtime(None, require_office=False)
    inner = '''import ctypes, os, time
if os.fork() == 0:
    os.setsid()
    ctypes.CDLL(None).prctl(15, b'lm-wsl-detached', 0, 0, 0)
time.sleep(60)
'''
    command = sandbox._sandbox_command(runtime, [runtime.python, '-I', '-S', '-c', inner], source)
    sandbox._run(command, timeout, limit=64, cancelled=cancelled)
sandbox.run_linux_conversion = convert
worker.main(sandbox)
"""
    return subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)


def _send_request(process, **changes):
    header = _request(**changes)
    process.stdin.write(struct.pack("!I", len(header)) + header + b"rtfH")
    process.stdin.flush()


def _stop(process):
    if process.poll() is None:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream and not stream.closed:
            stream.close()


@pytest.mark.parametrize("mode,code,pdf", [("success", 0, b"%PDF-synthetic-complete"), ("failure", 4, b"")])
def test_real_bridge_cleans_material_after_completion(tmp_path, real_runtime, mode, code, pdf):
    process = _launch(tmp_path, mode)
    try:
        _send_request(process)
        process.wait(timeout=3)
        assert process.returncode == code, process.stderr.read().decode()
        assert process.stdout.read() == pdf
        directory = Path((tmp_path / "task-path").read_text())
        assert not directory.exists()
    finally:
        _stop(process)


def _descendants(parent):
    found, pending = set(), [parent]
    while pending:
        pid = pending.pop()
        try:
            children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        except FileNotFoundError:
            continue
        for child in map(int, children):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


@pytest.mark.parametrize("ending", ["eof", "missing_heartbeat", "deadline", "invalid_heartbeat", "sighup", "sigterm"])
def test_real_bridge_parent_loss_kills_detached_namespace_and_removes_material(tmp_path, real_runtime, ending):
    process = _launch(tmp_path, "detached")
    descriptors = []
    try:
        _send_request(process, timeout=1.2 if ending == "deadline" else 20)
        deadline = time.monotonic() + 2
        detached = None
        while time.monotonic() < deadline:
            for pid in _descendants(process.pid):
                try:
                    if Path(f"/proc/{pid}/comm").read_text().strip() == "lm-wsl-detached":
                        detached = pid
                        break
                except FileNotFoundError:
                    pass
            if detached is not None:
                break
            time.sleep(0.01)
        assert detached is not None, "synthetic grandchild never started"
        assert os.getpgid(detached) == detached
        descriptors = [os.pidfd_open(pid) for pid in _descendants(process.pid)]
        assert len(descriptors) >= 3
        if ending == "eof":
            process.stdin.close()
        elif ending == "invalid_heartbeat":
            process.stdin.write(b"Q")
            process.stdin.flush()
        elif ending in ("sighup", "sigterm"):
            os.kill(process.pid, signal.SIGHUP if ending == "sighup" else signal.SIGTERM)
        process.wait(timeout=5)
        expected = 6 if ending == "deadline" else 2 if ending == "invalid_heartbeat" else 5
        assert process.returncode == expected, process.stderr.read().decode()
        assert process.stdout.read() == b""
        assert not Path((tmp_path / "task-path").read_text()).exists()
        with selectors.DefaultSelector() as selector:
            for descriptor in descriptors:
                selector.register(descriptor, selectors.EVENT_READ)
            deadline = time.monotonic() + 3
            while selector.get_map() and time.monotonic() < deadline:
                for key, _ in selector.select(max(0, deadline - time.monotonic())):
                    selector.unregister(key.fd)
            assert not selector.get_map(), "detached converter survived parent heartbeat loss"
    finally:
        _stop(process)
        for descriptor in descriptors:
            os.close(descriptor)


def test_real_bridge_oversized_header_is_rejected_without_task_files(tmp_path, real_runtime):
    process = _launch(tmp_path, "success")
    try:
        process.stdin.write(struct.pack("!I", worker.MAX_HEADER + 1))
        process.stdin.flush()
        process.wait(timeout=2)
        assert process.returncode == 2
        assert process.stdout.read() == b""
        assert not (tmp_path / "task-path").exists()
    finally:
        _stop(process)


def test_real_bridge_truncated_material_is_removed_without_running_converter(tmp_path, real_runtime):
    process = _launch(tmp_path, "success")
    try:
        header = _request(length=20)
        process.stdin.write(struct.pack("!I", len(header)) + header + b"partial")
        process.stdin.close()
        process.wait(timeout=3)
        assert process.returncode == 5
        assert process.stdout.read() == b""
        assert not Path((tmp_path / "task-path").read_text()).exists()
    finally:
        _stop(process)


def test_real_bridge_output_backpressure_still_honors_heartbeat_cancellation(tmp_path, real_runtime):
    process = _launch(tmp_path, "backpressure")
    try:
        _send_request(process)
        # Deliberately leave stdout unread while conversion fills the pipe.
        # EOF/heartbeat checks must remain active during output delivery.
        process.wait(timeout=5)
        assert process.returncode == 5, process.stderr.read().decode()
        assert len(process.stdout.read()) < 1024**2
        assert not Path((tmp_path / "task-path").read_text()).exists()
    finally:
        _stop(process)
