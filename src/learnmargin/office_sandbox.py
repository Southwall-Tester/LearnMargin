"""Route optional legacy Office conversion through mandatory OS isolation."""
from __future__ import annotations

import sys
from pathlib import Path

PLATFORM = sys.platform


class OfficeSandboxError(ValueError):
    """An authored, credential-free message suitable for the import API."""


def sandbox_backend(executable: str | None = None) -> str | None:
    """Report installed prerequisites; actual isolation is checked on each run."""
    if PLATFORM == "win32":
        from .wsl_sandbox import static_wsl_support
        return "windows-wsl" if static_wsl_support() else None
    if PLATFORM == "linux":
        from .linux_sandbox import static_linux_support
        return "linux-bubblewrap" if static_linux_support(executable) else None
    return None


def convert_office(executable: str | None, source: Path, output: Path, *, timeout: float = 120) -> None:
    """There is deliberately no direct subprocess/unsafe compatibility path."""
    try:
        if PLATFORM == "win32":
            from .wsl_sandbox import SandboxConversionError, SandboxUnavailable, run_wsl_conversion
            try:
                run_wsl_conversion(source, output, timeout=timeout)
            except (SandboxUnavailable, SandboxConversionError) as error:
                raise OfficeSandboxError(str(error)) from None
        elif PLATFORM == "linux":
            from .linux_sandbox import (
                SandboxConversionError,
                SandboxUnavailable,
                run_linux_conversion,
            )
            if not executable:
                raise OfficeSandboxError("隔离转换需要 LibreOffice，请安装系统转换依赖或自行导出 PDF。")
            try:
                run_linux_conversion(executable, source, output, timeout=timeout)
            except (SandboxConversionError, SandboxUnavailable) as error:
                raise OfficeSandboxError(str(error)) from None
        else:
            raise OfficeSandboxError("当前系统没有受支持的 Office 转换沙箱，请先导出 PDF 后导入。")
    except OfficeSandboxError:
        raise
    except TimeoutError:
        raise OfficeSandboxError("隔离转换超时，已停止转换进程，请拆分材料或自行导出 PDF。") from None
    except (OSError, RuntimeError):
        raise OfficeSandboxError("无法建立或完成隔离转换，请检查沙箱依赖或自行导出 PDF。") from None
