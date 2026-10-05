"""Windows host transport checks; Linux namespace checks live with its helper."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from learnmargin import wsl_sandbox as sandbox


@pytest.mark.parametrize("name", ["", "--exec", "name with spaces", "../other", "x\n--root", "x" * 65])
def test_reject_invalid_distribution_name(monkeypatch, name):
    monkeypatch.setenv(sandbox.DISTRIBUTION_ENV, name)
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox._distribution()


def test_only_named_wsl_distribution_and_fixed_user(monkeypatch):
    monkeypatch.setenv(sandbox.DISTRIBUTION_ENV, "LearnMargin-Office-Audit-ab12")
    monkeypatch.setattr(sandbox, "_wsl_executable", lambda: Path("fixed-wsl.exe"))
    monkeypatch.setattr(sandbox, "_registered_distribution", lambda name: name.endswith("ab12"))
    monkeypatch.setattr(sandbox, "_bootstrap", lambda: "trusted_source()")
    command = sandbox._command()
    assert command == ["fixed-wsl.exe", "--distribution", "LearnMargin-Office-Audit-ab12",
                       "--user", "learnmargin", "--cd", "/", "--exec", "/usr/bin/python3", "-I", "-S", "-u",
                       "-c", "trusted_source()"]


def test_unregistered_distribution_never_falls_back(monkeypatch):
    monkeypatch.setattr(sandbox, "_wsl_executable", lambda: Path("fixed-wsl.exe"))
    monkeypatch.setattr(sandbox, "_registered_distribution", lambda _: False)
    assert sandbox.static_wsl_support() is False
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox._command()


def test_read_input_bounds_real_file_and_rejects_hardlink(tmp_path, monkeypatch):
    source = tmp_path / "source.rtf"
    source.write_bytes(b"fixture")
    assert sandbox._read_input(source) == b"fixture"
    monkeypatch.setattr(sandbox, "MAX_BYTES", 3)
    with pytest.raises(sandbox.SandboxConversionError):
        sandbox._read_input(source)
    monkeypatch.setattr(sandbox, "MAX_BYTES", 100)
    alias = tmp_path / "alias.rtf"
    os.link(source, alias)
    with pytest.raises(sandbox.SandboxConversionError):
        sandbox._read_input(alias)


def test_read_input_rejects_symlink(tmp_path):
    source = tmp_path / "source.rtf"
    source.write_bytes(b"fixture")
    link = tmp_path / "link.rtf"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("This account cannot create symlinks")
    with pytest.raises(sandbox.SandboxConversionError):
        sandbox._read_input(link)


def test_output_is_exclusive_and_only_created_after_success(tmp_path, monkeypatch):
    source, output = tmp_path / "source.rtf", tmp_path / "result.pdf"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(sandbox, "_command", lambda: ["trusted"])
    def failed(*_):
        raise sandbox.SandboxConversionError("固定失败消息")
    monkeypatch.setattr(sandbox, "_exchange", failed)
    with pytest.raises(sandbox.SandboxConversionError):
        sandbox.run_wsl_conversion(source, output)
    assert not output.exists()
    monkeypatch.setattr(sandbox, "_exchange", lambda *_: b"%PDF-fixture")
    sandbox.run_wsl_conversion(source, output)
    assert output.read_bytes() == b"%PDF-fixture"
    with pytest.raises(FileExistsError):
        sandbox.run_wsl_conversion(source, output)


def test_environment_never_forwards_application_secrets(monkeypatch):
    monkeypatch.setenv("LEARNMARGIN_PROBE_SECRET", "private")
    monkeypatch.setenv("WSLENV", "LEARNMARGIN_PROBE_SECRET/u")
    monkeypatch.setenv("PYTHONPATH", "private-module-directory")
    assert "LEARNMARGIN_PROBE_SECRET" not in sandbox._environment()
    assert "PYTHONPATH" not in sandbox._environment()
    assert sandbox._environment()["WSLENV"] == ""


READ_REQUEST = """
import json, os, struct, sys
stream = sys.stdin.buffer
length = struct.unpack('!I', stream.read(4))[0]
header = json.loads(stream.read(length))
assert header == {'version': 1, 'suffix': '.rtf', 'length': 7, 'timeout': 5}
assert stream.read(header['length']) == b'fixture'
assert stream.read(1) == b'H'
assert 'LEARNMARGIN_PROBE_SECRET' not in os.environ
"""


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object transport")
def test_native_host_protocol_success(monkeypatch):
    monkeypatch.setenv("LEARNMARGIN_PROBE_SECRET", "private")
    script = READ_REQUEST + "sys.stdout.buffer.write(b'%PDF-fixture');sys.stdout.buffer.flush()"
    assert sandbox._exchange([sys.executable, "-I", "-S", "-c", script],
                             b"fixture", ".rtf", 5) == b"%PDF-fixture"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object transport")
@pytest.mark.parametrize("script,error", [
    ("import sys;sys.stdout.buffer.write(b'%PDF-partial');sys.exit(4)", sandbox.SandboxConversionError),
    ("import sys;sys.exit(3)", sandbox.SandboxUnavailable),
    ("import sys;sys.stdout.buffer.write(b'not-a-pdf')", sandbox.SandboxConversionError),
])
def test_native_host_rejects_failure_partial_and_invalid_output(script, error):
    with pytest.raises(error):
        sandbox._exchange([sys.executable, "-I", "-S", "-c", script], b"fixture", ".rtf", 5)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object transport")
def test_native_host_rejects_oversized_stdout_even_if_process_exits_zero(monkeypatch):
    monkeypatch.setattr(sandbox, "MAX_BYTES", 32)
    script = "import sys;sys.stdout.buffer.write(b'%PDF-'+b'x'*64)"
    with pytest.raises(sandbox.SandboxConversionError):
        sandbox._exchange([sys.executable, "-I", "-S", "-c", script], b"fixture", ".rtf", 5)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object transport")
def test_native_host_cancellation_closes_stdin_before_killing_wrapper(tmp_path):
    marker = tmp_path / "eof-observed"
    script = ("import pathlib,sys,time;sys.stdin.buffer.read();"
              f"pathlib.Path({str(marker)!r}).write_text('eof');time.sleep(.1)")
    with pytest.raises(sandbox.SandboxConversionError, match="超时"):
        sandbox._exchange([sys.executable, "-I", "-S", "-c", script], b"fixture", ".rtf", .3)
    assert marker.read_text() == "eof"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Job Object transport")
def test_native_host_timeout_also_cancels_a_blocked_material_write():
    started = time.monotonic()
    script = "import time;time.sleep(60)"
    with pytest.raises(sandbox.SandboxConversionError, match="超时"):
        sandbox._exchange([sys.executable, "-I", "-S", "-c", script],
                          b"x" * (1024 * 1024), ".rtf", .2)
    assert time.monotonic() - started < 9


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt" or not os.environ.get("LEARNMARGIN_TEST_OFFICE_WSL_DISTRIBUTION"),
                    reason="Explicit dedicated WSL sandbox integration environment required")
def test_real_dedicated_wsl_libreoffice_conversion(tmp_path, monkeypatch):
    from pypdf import PdfReader

    monkeypatch.setenv(sandbox.DISTRIBUTION_ENV, os.environ["LEARNMARGIN_TEST_OFFICE_WSL_DISTRIBUTION"])
    assert sandbox.static_wsl_support()
    source, output = tmp_path / "source.rtf", tmp_path / "result.pdf"
    source.write_bytes(br"{\rtf1\ansi LearnMargin isolated WSL conversion.}")
    sandbox.run_wsl_conversion(source, output, timeout=30)
    assert "LearnMargin isolated WSL conversion." in PdfReader(output).pages[0].extract_text()


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt" or not os.environ.get("LEARNMARGIN_TEST_OFFICE_WSL_DISTRIBUTION"),
                    reason="Explicit dedicated WSL sandbox integration environment required")
def test_real_windows_rtf_upload_runs_outer_worker_and_wsl_sandbox(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from learnmargin.app import create_app

    monkeypatch.setenv(sandbox.DISTRIBUTION_ENV, os.environ["LEARNMARGIN_TEST_OFFICE_WSL_DISTRIBUTION"])
    monkeypatch.setenv("LEARNMARGIN_ALLOW_LOCAL_OFFICE", "1")
    application = create_app(tmp_path / "isolated-app-data")
    with TestClient(application) as client:
        uploaded = client.post("/api/documents", files={
            "file": ("synthetic.rtf", br"{\rtf1\ansi LearnMargin Windows WSL upload integration test.}",
                     "application/rtf"),
        })
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["id"]
        unit = client.get(f"/api/documents/{document_id}/units/1")
        assert unit.status_code == 200
        assert "LearnMargin Windows WSL upload integration test" in unit.json()["text"]
        assert unit.json()["images"]
        for url in unit.json()["images"]:
            image = client.get(url)
            assert image.status_code == 200
            assert image.headers["content-type"] == "image/png"
            assert image.content.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt" or not os.environ.get("LEARNMARGIN_TEST_OFFICE_WSL_DISTRIBUTION"),
                    reason="Explicit dedicated WSL sandbox integration environment required")
def test_real_windows_owner_death_reaps_wsl_detached_child_and_input(tmp_path, monkeypatch):
    monkeypatch.setenv(sandbox.DISTRIBUTION_ENV, os.environ["LEARNMARGIN_TEST_OFFICE_WSL_DISTRIBUTION"])
    tag = uuid.uuid4().hex[:12]
    names = ["lmP" + tag, "lmC" + tag]
    marker = "/tmp/learnmargin-owner-probe-" + tag
    # Replace only the trusted synthetic converter command. The real WSL
    # transport, helper protocol, namespaces and cancellation all remain active.
    driver = f"""
import ctypes, os, time
name = {names[0]!r}
if os.fork() == 0:
    os.setsid()
    name = {names[1]!r}
ctypes.CDLL(None).prctl(15, name.encode(), 0, 0, 0)
time.sleep(60)
"""
    patch = f"""
from pathlib import Path
original_temporary = b.tempfile.TemporaryDirectory
def temporary(*args, **kwargs):
    result = original_temporary(*args, **kwargs)
    Path({marker!r}).write_text(result.name)
    return result
b.tempfile.TemporaryDirectory = temporary
a._CONVERT = {driver!r}
"""
    source, output = tmp_path / "source.rtf", tmp_path / "result.pdf"
    source.write_bytes(br"{\rtf1\ansi synthetic owner death fixture.}")
    script = f"""
import sys
sys.path.insert(0, {str(Path(sandbox.__file__).parents[1])!r})
from learnmargin import wsl_sandbox as sandbox
from pathlib import Path
original_bootstrap = sandbox._bootstrap
sandbox._bootstrap = lambda: original_bootstrap().replace('b.main(a)', {('exec(' + repr(patch) + ');b.main(a)')!r})
sandbox.run_wsl_conversion(Path({str(source)!r}), Path({str(output)!r}), timeout=30)
"""
    observer = f"""
import json, pathlib
found = {{}}
for entry in pathlib.Path('/proc').iterdir():
    if not entry.name.isdigit():
        continue
    try:
        name = (entry / 'comm').read_text().strip()
        if name in {names!r}:
            status = (entry / 'stat').read_text().split(') ', 1)[1].split()
            found[name] = [int(entry.name), int(status[2])]
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass
marker = pathlib.Path({marker!r})
task = marker.read_text() if marker.exists() else None
print(json.dumps({{'processes': found, 'task': task, 'task_exists': bool(task and pathlib.Path(task).exists())}}))
"""
    observer_command = [str(sandbox._wsl_executable()), "--distribution", sandbox._distribution(),
                        "--user", "learnmargin", "--cd", "/", "--exec", "/usr/bin/python3", "-I", "-S", "-c"]
    def observe():
        result = subprocess.run([*observer_command, observer], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=5, check=True,
                                env=sandbox._environment(), creationflags=subprocess.CREATE_NO_WINDOW)
        return json.loads(result.stdout)
    # Use the base interpreter directly: killing a Windows venv launcher alone
    # would not be the same as killing the process that owns the private Job.
    process = subprocess.Popen([sys._base_executable, "-I", "-S", "-c", script],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               env={**sandbox._environment(), sandbox.DISTRIBUTION_ENV: sandbox._distribution()},
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 10
        before = observe()
        while set(before["processes"]) != set(names) and time.monotonic() < deadline:
            assert process.poll() is None, process.stderr.read().decode(errors="replace")
            time.sleep(.1)
            before = observe()
        assert set(before["processes"]) == set(names), before
        # A real detached session exists, not merely a failed child launch.
        child_pid, child_group = before["processes"][names[1]]
        assert child_pid == child_group
        assert before["task_exists"]
        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 7
        after = observe()
        while (after["processes"] or after["task_exists"]) and time.monotonic() < deadline:
            time.sleep(.1)
            after = observe()
        assert not after["processes"], after
        assert not after["task_exists"], after
        assert not output.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()
        # Remove only this test's exact, synthetic marker. The helper owns and
        # removes its private /dev/shm task directory itself.
        subprocess.run([*observer_command, f"from pathlib import Path;Path({marker!r}).unlink(missing_ok=True)"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                       env=sandbox._environment(), creationflags=subprocess.CREATE_NO_WINDOW, check=True)
