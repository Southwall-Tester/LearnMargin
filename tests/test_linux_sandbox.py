"""Boundary checks plus real Linux namespace tests (without private documents)."""
import errno
import os
import selectors
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from learnmargin import linux_sandbox as sandbox


@pytest.fixture
def runtime():
    return sandbox.LinuxRuntime("/usr/bin/bwrap", "/usr/bin/python3",
                                "/usr/lib/libreoffice/program/soffice.bin",
                                (("/usr/lib", "/usr/lib"), ("/lib", "/lib")))


def test_command_has_no_host_writable_mounts_or_ambient_environment(runtime, tmp_path):
    source = tmp_path / "source.rtf"
    command = sandbox._sandbox_command(runtime, [runtime.python, "-c", "pass"], source)
    for required in ("--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc",
                     "--unshare-uts", "--disable-userns", "--assert-userns-disabled",
                     "--new-session", "--die-with-parent", "--clearenv"):
        assert required in command
    for forbidden in ("--bind", "--dev-bind", "--share-net", "--unshare-user-try",
                      "--not-a-security-boundary"):
        assert forbidden not in command
    mounts = [(command[i + 1], command[i + 2])
              for i, value in enumerate(command) if value == "--ro-bind"]
    assert mounts == [*runtime.mounts, (str(source), "/input/source.rtf")]
    assert command[command.index("--cap-drop") + 1] == "ALL"
    tmpfs = [command[i + 1] for i, value in enumerate(command) if value == "--tmpfs"]
    assert set(tmpfs) == {"/dev/shm", "/tmp", "/work", "/home", "/run"}
    assert command.count("--size") == len(tmpfs)


def test_static_probe_does_not_launch_processes(monkeypatch, runtime):
    monkeypatch.setattr(sandbox, "_runtime", lambda _executable: runtime)
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("spawned"))
    assert sandbox.static_linux_support("/usr/bin/libreoffice")


def test_missing_isolation_fails_closed(monkeypatch, tmp_path):
    source = tmp_path / "source.rtf"
    source.write_bytes(b"{\\rtf1 test}")
    def unavailable(_executable):
        raise sandbox.SandboxUnavailable("missing")
    monkeypatch.setattr(sandbox, "_runtime", unavailable)
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("spawned"))
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.run_linux_conversion("/usr/bin/libreoffice", source, tmp_path / "result.pdf")
    assert not (tmp_path / "result.pdf").exists()
    assert not sandbox.static_linux_support("/usr/bin/libreoffice")
    assert not sandbox.linux_sandbox_available("/usr/bin/libreoffice")


@pytest.mark.parametrize("content", [b"", b"not a pdf"])
def test_invalid_output_is_never_written(monkeypatch, tmp_path, runtime, content):
    source = tmp_path / "source.rtf"
    source.write_bytes(b"{\\rtf1 test}")
    monkeypatch.setattr(sandbox, "_runtime", lambda _: runtime)
    monkeypatch.setattr(sandbox, "_run", lambda *_args, **_kwargs: content)
    with pytest.raises(sandbox.SandboxConversionError):
        sandbox.run_linux_conversion("office", source, tmp_path / "result.pdf")
    assert not (tmp_path / "result.pdf").exists()


def test_pdf_only_written_after_success_without_overwrite(monkeypatch, tmp_path, runtime):
    source, output = tmp_path / "source.rtf", tmp_path / "result.pdf"
    source.write_bytes(b"{\\rtf1 test}")
    monkeypatch.setattr(sandbox, "_runtime", lambda _: runtime)
    def run(_command, _timeout, *, limit):
        assert not output.exists()
        assert limit == sandbox.MAX_PDF_BYTES
        return b"%PDF-synthetic"
    monkeypatch.setattr(sandbox, "_run", run)
    sandbox.run_linux_conversion("office", source, output)
    assert output.read_bytes() == b"%PDF-synthetic"
    monkeypatch.setattr(sandbox, "_run", lambda *_args, **_kwargs: b"%PDF-replacement")
    with pytest.raises(FileExistsError):
        sandbox.run_linux_conversion("office", source, output)
    assert output.read_bytes() == b"%PDF-synthetic"


def test_invalid_input_never_enters_parser(monkeypatch, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"synthetic")
    monkeypatch.setattr(sandbox, "_runtime", lambda _: pytest.fail("invalid input used"))
    with pytest.raises(sandbox.SandboxConversionError):
        sandbox.run_linux_conversion("office", source, tmp_path / "result.pdf")


@pytest.fixture
def real_runtime():
    if sys.platform != "linux":
        pytest.skip("Linux namespaces are not available on this OS")
    command = None
    try:
        runtime = sandbox._runtime(None, require_office=False)
        command = sandbox._sandbox_command(runtime, [runtime.python, "-I", "-S", "-c", "print('ok')"])
        assert sandbox._run(command, 5, limit=64) == b"ok\n"
    except (sandbox.SandboxUnavailable, sandbox.SandboxConversionError):
        if os.environ.get("LEARNMARGIN_REQUIRE_SANDBOX_TESTS") == "1":
            diagnostic = ""
            if command is not None:
                # This is the fixed print('ok') probe, with no uploaded input
                # or inherited environment. Keep raw OS diagnostics in tests,
                # never in product responses to document uploads.
                probe = subprocess.run(command, capture_output=True, timeout=5,
                                       env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
                diagnostic = probe.stderr.decode(errors="replace")[:1500]
            pytest.fail(f"Required Linux sandbox could not be established: {diagnostic}")
        pytest.skip("bubblewrap / user namespaces unavailable")
    return runtime


def _python(runtime, source, *, input_file=None, timeout=5, limit=65536):
    command = sandbox._sandbox_command(runtime, [runtime.python, "-I", "-S", "-c", source], input_file)
    return sandbox._run(command, timeout, limit=limit)


def test_real_linux_cannot_read_or_write_host_files(real_runtime, tmp_path, monkeypatch):
    secret = tmp_path / "private-canary.txt"
    secret.write_text("host private value", encoding="utf-8")
    monkeypatch.setenv("LEARNMARGIN_TEST_SECRET", "must-not-enter-sandbox")
    source = tmp_path / "source.rtf"
    source.write_bytes(b"{\\rtf1 material}")
    script = f"""
import os, pathlib
assert not pathlib.Path({str(secret)!r}).exists()
assert not pathlib.Path('/mnt/c/Users').exists()
assert not pathlib.Path('/run/user').exists()
assert 'LEARNMARGIN_TEST_SECRET' not in os.environ
source = pathlib.Path('/input/source.rtf')
assert source.read_bytes() == b'{{\\\\rtf1 material}}'
try:
    source.write_text('changed')
except OSError:
    pass
else:
    raise AssertionError('input is writable')
assert 'NoNewPrivs:\\t1' in pathlib.Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in pathlib.Path('/proc/self/status').read_text()
print('isolated')
"""
    assert _python(real_runtime, script, input_file=source) == b"isolated\n"
    assert secret.read_text(encoding="utf-8") == "host private value"
    assert source.read_bytes() == b"{\\rtf1 material}"


def test_real_linux_cannot_reach_host_loopback(real_runtime):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        script = f"""
import socket
s = socket.socket()
s.settimeout(0.2)
try:
    s.connect(('127.0.0.1', {port}))
except OSError:
    print('blocked')
else:
    raise AssertionError('host network reachable')
"""
        assert _python(real_runtime, script) == b"blocked\n"


def test_real_linux_tmpfs_has_aggregate_quota(real_runtime, monkeypatch):
    monkeypatch.setattr(sandbox, "WORK_BYTES", 1024 * 1024)
    script = f"""
import errno, pathlib
try:
    for i in range(4):
        pathlib.Path('/work/' + str(i)).write_bytes(b'x' * (512 * 1024))
except OSError as exc:
    assert exc.errno == {errno.ENOSPC}
    print('bounded')
else:
    raise AssertionError('tmpfs quota absent')
"""
    assert _python(real_runtime, script) == b"bounded\n"


def test_real_linux_response_pipe_has_parent_limit(real_runtime):
    with pytest.raises(sandbox.SandboxConversionError, match="超过"):
        _python(real_runtime, "import os; os.write(1, b'x' * 2048)", limit=1024)


def test_real_linux_slow_process_is_terminated(real_runtime):
    with pytest.raises(sandbox.SandboxConversionError, match="超时"):
        _python(real_runtime, "import time; time.sleep(10)", timeout=0.2)


def _owned_descendants(parent):
    """Inspect only this test's own worker tree, never global process names."""
    found = set()
    pending = [parent]
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


@pytest.mark.parametrize("ending", ["timeout", "parent_cancellation"])
def test_real_linux_detached_grandchild_cannot_survive_worker(ending, real_runtime):
    # setsid deliberately escapes the host process group. PID-namespace
    # teardown must still kill it when the bwrap parent or worker disappears.
    inner = """
import ctypes, os, time
child = os.fork()
if child == 0:
    os.setsid()
    ctypes.CDLL(None).prctl(15, b'lm-detached', 0, 0, 0)
    time.sleep(60)
else:
    time.sleep(60)
"""
    timeout = 3 if ending == "timeout" else 30
    worker_script = f"""
import os
from learnmargin import linux_sandbox as sandbox
os.environ['LEARNMARGIN_EXTRACTION_WORKER'] = '1'
runtime = sandbox._runtime(None, require_office=False)
command = sandbox._sandbox_command(runtime, [runtime.python, '-I', '-S', '-c', {inner!r}])
try:
    sandbox._run(command, {timeout}, limit=64)
except sandbox.SandboxConversionError as error:
    if '超时' not in str(error):
        raise
"""
    worker = subprocess.Popen([sys.executable, "-c", worker_script],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE, start_new_session=True)
    descriptors = []
    try:
        deadline = time.monotonic() + 2.5
        detached = None
        while time.monotonic() < deadline:
            descendants = _owned_descendants(worker.pid)
            for pid in descendants:
                try:
                    name = Path(f"/proc/{pid}/comm").read_text().strip()
                    if name == "lm-detached":
                        detached = pid
                        break
                except FileNotFoundError:
                    pass
            if detached is not None:
                break
            time.sleep(0.01)
        assert detached is not None, "sandbox grandchild never reached setsid"
        assert os.getpgid(detached) == detached
        assert os.getpgid(detached) != worker.pid
        # Stable pidfds avoid confusing a recycled PID with this owned process.
        descriptors = [os.pidfd_open(pid) for pid in _owned_descendants(worker.pid)]
        assert len(descriptors) >= 3
        if ending == "parent_cancellation":
            # Kill only the worker, not its group; bwrap's PDEATHSIG chain and
            # namespace teardown are responsible for all descendant cleanup.
            worker.kill()
        worker.wait(timeout=5)
        if ending == "timeout":
            assert worker.returncode == 0, worker.stderr.read().decode(errors="replace")
        with selectors.DefaultSelector() as selector:
            for descriptor in descriptors:
                selector.register(descriptor, selectors.EVENT_READ)
            deadline = time.monotonic() + 5
            while selector.get_map() and time.monotonic() < deadline:
                for key, _ in selector.select(max(0, deadline - time.monotonic())):
                    selector.unregister(key.fd)
            assert not selector.get_map(), "a detached sandbox descendant survived"
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
        if worker.stderr:
            worker.stderr.close()
        for descriptor in descriptors:
            os.close(descriptor)


def test_real_linux_libreoffice_rtf_conversion(tmp_path, real_runtime):
    from pypdf import PdfReader

    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        if os.environ.get("LEARNMARGIN_REQUIRE_SANDBOX_TESTS") == "1":
            pytest.fail("Required LibreOffice is not installed")
        pytest.skip("System LibreOffice is not installed")
    source, output = tmp_path / "source.rtf", tmp_path / "result.pdf"
    source.write_bytes(b"{\\rtf1\\ansi LearnMargin sandbox conversion test.}")
    try:
        sandbox.run_linux_conversion(executable, source, output)
    except sandbox.SandboxConversionError:
        # Diagnostics use only this known synthetic RTF and the identical
        # namespace policy. Production conversion never exposes parser stderr.
        runtime = sandbox._runtime(executable)
        driver = sandbox._CONVERT.replace("stderr=subprocess.DEVNULL", "stderr=None")
        command = sandbox._sandbox_command(runtime, [runtime.python, "-I", "-S", "-c", driver,
                                                   runtime.office, "/input/source.rtf"], source)
        probe = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, timeout=15)
        pytest.fail(f"Synthetic LibreOffice conversion failed: {probe.stderr.decode(errors='replace')[:3000]}")
    assert output.read_bytes().startswith(b"%PDF-")
    assert output.stat().st_size > 100
    document = PdfReader(output)
    assert len(document.pages) >= 1
    assert "LearnMargin sandbox conversion test" in "".join(page.extract_text() for page in document.pages)


def test_real_linux_rtf_upload_runs_worker_dispatcher_and_pdf_extraction(tmp_path, real_runtime, monkeypatch):
    if not (shutil.which("libreoffice") or shutil.which("soffice")):
        if os.environ.get("LEARNMARGIN_REQUIRE_SANDBOX_TESTS") == "1":
            pytest.fail("Required LibreOffice is not installed")
        pytest.skip("System LibreOffice is not installed")
    from fastapi.testclient import TestClient

    from learnmargin.app import create_app

    monkeypatch.setenv("LEARNMARGIN_ALLOW_LOCAL_OFFICE", "1")
    application = create_app(tmp_path / "isolated-app-data")
    with TestClient(application) as client:
        uploaded = client.post("/api/documents", files={
            "file": ("synthetic.rtf", b"{\\rtf1\\ansi LearnMargin sandbox upload integration test.}",
                     "application/rtf"),
        })
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["id"]
        unit = client.get(f"/api/documents/{document_id}/units/1")
        assert unit.status_code == 200
        assert "LearnMargin sandbox upload integration test" in unit.json()["text"]
        images = unit.json()["images"]
        assert images
        document = application.state.store.document(document_id)
        folder = application.state.store.directory("documents", document_id)
        assert all((folder / name).is_file() for name in document.units[0].image_paths)
        for url in images:
            image = client.get(url)
            assert image.status_code == 200
            assert image.headers["content-type"] == "image/png"
            assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
