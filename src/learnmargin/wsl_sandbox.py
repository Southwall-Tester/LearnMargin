"""Windows transport for a dedicated WSL Linux/bubblewrap Office sandbox.

Only a trusted helper runs outside bubblewrap. Document bytes cross anonymous
pipes; neither document paths nor Windows mounts enter the converter namespace.
Killing wsl.exe is not a Linux process-tree kill: EOF and a bounded heartbeat
watchdog in the Linux helper provide that independent cancellation path.
"""
from __future__ import annotations

import base64
import ctypes
import json
import math
import os
import re
import stat
import struct
import subprocess
import threading
import time
import zlib
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024
DISTRIBUTION_ENV = "LEARNMARGIN_OFFICE_WSL_DISTRIBUTION"
DEFAULT_DISTRIBUTION = "LearnMargin-Office"
UNAVAILABLE = "未找到可用的专用 WSL 转换环境，请配置 LearnMargin-Office 或先导出 PDF。"
FAILED = "隔离转换失败，请检查专用 WSL 环境或用原应用导出 PDF。"
TIMED_OUT = "隔离转换超时，请拆分材料或先导出 PDF。"


class SandboxUnavailable(ValueError):
    """The configured isolation prerequisites are unavailable."""


class SandboxConversionError(ValueError):
    """The isolated request failed or exceeded a bound."""


def _distribution() -> str:
    name = os.environ.get(DISTRIBUTION_ENV, DEFAULT_DISTRIBUTION)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise SandboxUnavailable(UNAVAILABLE)
    return name


def _wsl_executable() -> Path:
    if os.name != "nt":
        raise SandboxUnavailable(UNAVAILABLE)
    root = os.environ.get("SystemRoot", r"C:\Windows")
    executable = Path(root) / "System32" / "wsl.exe"
    if not executable.is_file():
        raise SandboxUnavailable(UNAVAILABLE)
    return executable


def _registered_distribution(name: str) -> bool:
    """Read only WSL registration metadata; do not start a distro in settings."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Lxss") as root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                    try:
                        found = winreg.QueryValueEx(entry, "DistributionName")[0]
                        version = winreg.QueryValueEx(entry, "Version")[0]
                    except OSError:
                        continue
                    if found.casefold() == name.casefold() and version == 2:
                        return True
    except OSError:
        pass
    return False


def static_wsl_support() -> bool:
    """Report WSL 2 registration, with live boundary checks on every conversion."""
    try:
        _wsl_executable()
        return _registered_distribution(_distribution())
    except (SandboxUnavailable, OSError, ImportError):
        return False


def _bootstrap() -> str:
    directory = Path(__file__).parent
    sources = [(directory / name).read_text(encoding="utf-8")
               for name in ("linux_sandbox.py", "wsl_worker.py")]
    packed = base64.b64encode(zlib.compress(json.dumps(sources).encode("utf-8"), 9)).decode("ascii")
    # Executed by /usr/bin/python3 -I -S: no shell, site hooks or installed app
    # dependencies, and no material-derived code or arguments in this bootstrap.
    return (
        "import base64,json,sys,types,zlib;"
        f"s=json.loads(zlib.decompress(base64.b64decode('{packed}')));"
        "a=types.ModuleType('_learnmargin_linux_sandbox');"
        "b=types.ModuleType('_learnmargin_wsl_worker');"
        "sys.modules[a.__name__]=a;sys.modules[b.__name__]=b;"
        "exec(compile(s[0],'<learnmargin-linux-sandbox>','exec'),a.__dict__);"
        "exec(compile(s[1],'<learnmargin-wsl-worker>','exec'),b.__dict__);b.main(a)"
    )


def _command() -> list[str]:
    name = _distribution()
    executable = _wsl_executable()
    if not _registered_distribution(name):
        raise SandboxUnavailable(UNAVAILABLE)
    command = [str(executable), "--distribution", name, "--user", "learnmargin",
               "--cd", "/", "--exec", "/usr/bin/python3", "-I", "-S", "-u", "-c", _bootstrap()]
    if len(subprocess.list2cmdline(command)) > 30000:
        raise SandboxUnavailable(UNAVAILABLE)
    return command


def _read_input(path: Path) -> bytes:
    """Do not follow reparse points; bound the actual open file, not a path stat."""
    if path.absolute() != path.resolve() or path.suffix.lower() not in {".doc", ".ppt", ".rtf", ".odt", ".odp"}:
        raise SandboxConversionError("转换材料路径无效，请重新上传。")
    if os.name == "nt":
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # GENERIC_READ, no sharing, OPEN_EXISTING, OPEN_REPARSE_POINT.
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise SandboxConversionError("无法读取转换材料，请重新上传。")
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            kernel.CloseHandle(handle)
            raise
    else:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & 0x400
                or not 0 < info.st_size <= MAX_BYTES):
            raise SandboxConversionError("转换材料不是独立的普通文件，或大小超过 50 MB。")
        data = stream.read(MAX_BYTES + 1)
        if not 0 < len(data) <= MAX_BYTES:
            raise SandboxConversionError("转换材料大小无效，请拆分材料后上传。")
        return data


def _environment() -> dict[str, str]:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return {"SystemRoot": system_root, "WINDIR": system_root,
            "PATH": str(Path(system_root) / "System32"), "WSLENV": ""}


def _cancel_io(thread: threading.Thread) -> None:
    """Cancel only the synchronous pipe operation of our dedicated IO thread."""
    if not thread.is_alive() or thread.native_id is None:
        return
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
    kernel.CancelSynchronousIo.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenThread(0x1, False, thread.native_id)  # THREAD_TERMINATE access, never terminate it.
    if handle:
        try:
            kernel.CancelSynchronousIo(handle)
        finally:
            kernel.CloseHandle(handle)


def _exchange(command: list[str], data: bytes, suffix: str, timeout: float) -> bytes:
    from .extraction_worker import _WindowsJob

    header = json.dumps({"version": 1, "suffix": suffix, "length": len(data),
                         "timeout": timeout}, separators=(",", ":")).encode("utf-8")
    packet = struct.pack("!I", len(header)) + header
    stop = threading.Event()
    reader_done = threading.Event()
    errors: list[BaseException] = []
    chunks: list[bytes] = []
    process = None
    job = None
    threads: list[threading.Thread] = []
    deadline = time.monotonic() + timeout

    def write_request():
        try:
            for block in (packet, memoryview(data)):
                for offset in range(0, len(block), 65536):
                    remaining = block[offset:offset + 65536]
                    while remaining and not stop.is_set():
                        count = process.stdin.write(remaining)
                        if not count:
                            raise BrokenPipeError
                        remaining = remaining[count:]
            # Never catch up missed beats in a burst: buffered heartbeats must
            # not keep the Linux watchdog alive after the Windows owner dies.
            while not stop.is_set():
                process.stdin.write(b"H")
                if stop.wait(1):
                    break
        except (OSError, ValueError):
            # A completed helper may close stdin before the next heartbeat.
            # Its exit status and bounded stdout determine success. A blocked
            # or failed writer cannot extend the independent helper deadline.
            pass
        finally:
            process.stdin.close()

    def read_result():
        total = 0
        try:
            while chunk := process.stdout.read(65536):
                total += len(chunk)
                if total > MAX_BYTES:
                    errors.append(SandboxConversionError("转换后的 PDF 超过 50 MB，请拆分材料。"))
                    return
                chunks.append(chunk)
        except (OSError, ValueError) as error:
            errors.append(error)
        finally:
            process.stdout.close()
            reader_done.set()

    try:
        job = _WindowsJob()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, bufsize=0, close_fds=True,
                                   env=_environment(), creationflags=subprocess.CREATE_NO_WINDOW)
        # The trusted helper cannot parse a document before assignment because
        # no request bytes are sent until this host process is contained.
        job.attach(process.pid)
        threads = [threading.Thread(target=write_request, daemon=True),
                   threading.Thread(target=read_result, daemon=True)]
        for thread in threads:
            thread.start()
        while True:
            if errors:
                raise SandboxConversionError(FAILED)
            code = process.poll()
            if code is not None and reader_done.is_set():
                if code == 3:
                    raise SandboxUnavailable(UNAVAILABLE)
                if code == 6:
                    raise SandboxConversionError(TIMED_OUT)
                if code:
                    raise SandboxConversionError(FAILED)
                break
            if time.monotonic() >= deadline:
                raise SandboxConversionError(TIMED_OUT)
            reader_done.wait(0.05) if not reader_done.is_set() else time.sleep(0.05)
        result = b"".join(chunks)
        if not result.startswith(b"%PDF-"):
            raise SandboxConversionError("隔离转换未生成有效 PDF，请用原应用重新导出。")
        return result
    except (OSError, RuntimeError):
        raise SandboxUnavailable(UNAVAILABLE) from None
    finally:
        stop.set()
        try:
            if process is not None:
                if threads:
                    # FileIO.close from another thread can itself block behind
                    # WriteFile. Cancel first; the writer owns closing stdin
                    # and thus delivers EOF without holding up cleanup.
                    _cancel_io(threads[0])
                    threads[0].join(timeout=0.5)
                elif process.stdin is not None:
                    process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            try:
                if job is not None:
                    job.close()
            finally:
                if len(threads) > 1:
                    _cancel_io(threads[1])
                elif process is not None and process.stdout is not None:
                    process.stdout.close()
                for thread in threads:
                    thread.join(timeout=1)


def run_wsl_conversion(source: Path, output: Path, *, timeout: float = 120) -> None:
    """Write a bounded PDF only after helper success and Linux sandbox teardown."""
    if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise SandboxConversionError("转换时限无效。")
    source, output = Path(source), Path(output)
    command = _command()
    data = _read_input(source)
    result = _exchange(command, data, source.suffix.lower(), timeout)
    if output.parent.absolute() != output.parent.resolve():
        raise SandboxConversionError("转换输出路径无效。")
    with output.open("xb") as stream:
        stream.write(result)
