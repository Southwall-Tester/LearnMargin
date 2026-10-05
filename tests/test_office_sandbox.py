"""The local opt-in never authorizes an unsandboxed converter."""
import sys
from types import SimpleNamespace

import pytest

from learnmargin import office_sandbox as sandbox


def test_unsupported_platform_refuses_even_with_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "PLATFORM", "unsupported")
    monkeypatch.setenv("LEARNMARGIN_ALLOW_LOCAL_OFFICE", "1")
    assert sandbox.sandbox_backend("office") is None
    with pytest.raises(sandbox.OfficeSandboxError, match="没有受支持"):
        sandbox.convert_office("office", tmp_path / "source.doc", tmp_path / "result.pdf")
    assert not (tmp_path / "result.pdf").exists()


def test_linux_capability_uses_the_discovered_runtime(monkeypatch):
    monkeypatch.setattr(sandbox, "PLATFORM", "linux")
    def supported(executable):
        assert executable == "/usr/bin/libreoffice"
        return True
    monkeypatch.setitem(sys.modules, "learnmargin.linux_sandbox", SimpleNamespace(static_linux_support=supported))
    assert sandbox.sandbox_backend("/usr/bin/libreoffice") == "linux-bubblewrap"


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_declared_support_never_bypasses_real_launch_failure(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(sandbox, "PLATFORM", platform)
    calls = []
    class Refused(ValueError):
        pass
    def refuse(*arguments, **kwargs):
        calls.append((arguments, kwargs))
        raise Refused("无法建立转换沙箱。")
    if platform == "win32":
        module = SimpleNamespace(SandboxUnavailable=Refused, SandboxConversionError=Refused,
                                 run_wsl_conversion=refuse, static_wsl_support=lambda: True)
        name = "learnmargin.wsl_sandbox"
    else:
        module = SimpleNamespace(SandboxUnavailable=Refused, SandboxConversionError=Refused,
                                 run_linux_conversion=refuse, static_linux_support=lambda _: True)
        name = "learnmargin.linux_sandbox"
    monkeypatch.setitem(sys.modules, name, module)
    assert sandbox.sandbox_backend("office") is not None
    with pytest.raises(sandbox.OfficeSandboxError, match="沙箱"):
        sandbox.convert_office("office", tmp_path / "source.doc", tmp_path / "result.pdf", timeout=12)
    assert len(calls) == 1 and calls[0][1] == {"timeout": 12}
    assert len(calls[0][0]) == (2 if platform == "win32" else 3)
    assert not (tmp_path / "result.pdf").exists()


def test_unexpected_os_failure_does_not_echo_private_details(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "PLATFORM", "win32")
    class KnownError(ValueError):
        pass
    def fail(*args, **kwargs):
        raise OSError("private-file-canary")
    monkeypatch.setitem(sys.modules, "learnmargin.wsl_sandbox", SimpleNamespace(
        SandboxUnavailable=KnownError, SandboxConversionError=KnownError, run_wsl_conversion=fail))
    with pytest.raises(sandbox.OfficeSandboxError) as caught:
        sandbox.convert_office("office", tmp_path / "source.doc", tmp_path / "result.pdf")
    assert "private-file-canary" not in str(caught.value)


@pytest.mark.parametrize("enabled,executable,backend,available", [
    (False, None, "windows-wsl", False),
    (True, None, "windows-wsl", True),
    (True, None, None, False),
    (True, "office", None, False),
    (True, "office", "linux-bubblewrap", True),
])
def test_settings_require_opt_in_runtime_and_isolation(monkeypatch, tmp_path,
                                                      enabled, executable, backend, available):
    from fastapi.testclient import TestClient

    from learnmargin import app as application

    monkeypatch.setattr(application, "local_office_enabled", lambda: enabled)
    monkeypatch.setattr(application, "find_libreoffice", lambda: executable)
    calls = []

    def detect(path):
        calls.append(path)
        return backend

    monkeypatch.setattr(application, "sandbox_backend", detect)
    with TestClient(application.create_app(tmp_path)) as client:
        response = client.get("/api/settings")
    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert capabilities["libreoffice"] is available
    assert capabilities["office_sandbox"] == backend
    assert calls == [executable]
