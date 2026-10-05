"""Windows host transport checks; Linux namespace checks live with its helper."""
import os
import sys
import time
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
