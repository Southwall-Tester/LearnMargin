"""Resource-limited extraction processes, not an OS filesystem/network sandbox."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Document

WALL_SECONDS = 150
CPU_SECONDS = 120
MEMORY_BYTES = 2 * 1024**3
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESULT_BYTES = 16 * 1024**2
WINDOWS = os.name == "nt"
RESOURCE_ERROR = "文档解析未完成或达到资源限制，请拆分材料或用原应用重新导出后上传。"
LIMIT_ERROR = "无法建立文档解析资源限制，本次未读取材料。请检查系统环境后重试。"


class ExtractionError(ValueError):
    pass


def worker_environment() -> dict[str, str]:
    """Pass only OS/tool discovery settings, never arbitrary application secrets."""
    allowed = {
        "PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP", "TMPDIR",
        "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
        "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432", "LANG", "LC_ALL", "LC_CTYPE",
        "LEARNMARGIN_ALLOW_LOCAL_OFFICE", "LEARNMARGIN_OFFICE_WSL_DISTRIBUTION",
    }
    result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    result["LEARNMARGIN_EXTRACTION_WORKER"] = "1"
    return result


class _WindowsJob:
    """A private, non-inheritable job; descendants cannot opt out of its limits."""

    def __init__(self, memory_limit: int = MEMORY_BYTES):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
            ]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                     wintypes.DWORD, ctypes.c_void_p]
        kernel.QueryInformationJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.accounting_type = Accounting
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ExtractionError(LIMIT_ERROR)
        limits = ExtendedLimits()
        # KILL_ON_JOB_CLOSE | PROCESS_MEMORY | JOB_MEMORY | JOB_TIME | ACTIVE_PROCESS.
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x100 | 0x200 | 0x4 | 0x8
        limits.BasicLimitInformation.PerJobUserTimeLimit = CPU_SECONDS * 10_000_000
        limits.BasicLimitInformation.ActiveProcessLimit = 16
        limits.ProcessMemoryLimit = memory_limit
        limits.JobMemoryLimit = memory_limit
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ExtractionError(LIMIT_ERROR)

    def attach(self, pid: int) -> None:
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE, for this newly created worker only.
        process = self.kernel.OpenProcess(0x100 | 0x1, False, pid)
        if not process:
            raise ExtractionError(LIMIT_ERROR)
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise ExtractionError(LIMIT_ERROR)
        finally:
            self.kernel.CloseHandle(process)

    def close(self) -> None:
        if self.handle:
            import ctypes
            try:
                if not self.kernel.TerminateJobObject(self.handle, 1):
                    raise ExtractionError("无法确认文档解析进程已停止，请稍后重试。")
                deadline = time.monotonic() + 5
                while True:
                    accounting = self.accounting_type()
                    if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                                 ctypes.sizeof(accounting), None):
                        raise ExtractionError("无法确认文档解析进程已停止，请稍后重试。")
                    if accounting.ActiveProcesses == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise ExtractionError("无法确认文档解析进程已停止，请稍后重试。")
                    time.sleep(0.01)
            finally:
                # KILL_ON_JOB_CLOSE remains the final backstop even if querying
                # completion failed. A failure never counts as confirmed death.
                self.kernel.CloseHandle(self.handle)
                self.handle = None


def _create_limiter() -> _WindowsJob | None:
    return _WindowsJob() if WINDOWS else None


def _unix_limits() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


async def _spawn_worker() -> asyncio.subprocess.Process:
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {"start_new_session": True}
    return await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-u", "-m", "learnmargin.extraction_worker",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=worker_environment(), limit=65536, **options,
    )


async def _read_result(stream: asyncio.StreamReader) -> bytes:
    result = bytearray()
    while chunk := await stream.read(65536):
        if len(result) + len(chunk) > MAX_RESULT_BYTES:
            raise ExtractionError(RESOURCE_ERROR)
        result.extend(chunk)
    return bytes(result)


async def _terminate_worker(process, limiter) -> None:
    # Closing our private job kills any remaining descendants, even on success.
    limit_error = None
    if limiter is not None:
        try:
            limiter.close()
        except Exception as error:
            # Still reap the owned process and close its pipes. The caller must
            # preserve journals as unconfirmed even when these steps succeed.
            limit_error = error
    if not WINDOWS:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    if process.stdin is not None:
        process.stdin.close()

    async def reap():
        if process.stdout is not None:
            # Drain only after terminating the tree. Buffered pipe data is bounded
            # and discarded, so wait() cannot deadlock behind a full stdout pipe.
            while await process.stdout.read(65536):
                pass
        await process.wait()

    await asyncio.wait_for(reap(), timeout=10)
    if limit_error is not None:
        raise limit_error


async def _settle_owned_task(task):
    """Repeated cancellation must not abandon a just-spawned child or cleanup."""
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    return task.result(), interrupted


async def extract_in_worker(path: Path, output: Path, item_id: str, name: str | None = None) -> Document:
    """Extract in an owned process tree; cancel/timeout closes that tree only."""
    from .models import Document

    payload = json.dumps({"path": str(Path(path).resolve()), "output": str(Path(output).resolve()),
                          "item_id": item_id, "name": name}, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(payload) > MAX_REQUEST_BYTES:
        raise ExtractionError("材料文件名或路径过长，请缩短后上传。")
    limiter = None
    process = None
    spawn_task = None
    try:
        limiter = _create_limiter()
        async with asyncio.timeout(WALL_SECONDS):
            # Shield process creation so cancellation cannot lose a newly created
            # child before we obtain its PID and clean it up in finally.
            spawn_task = asyncio.create_task(_spawn_worker())
            process = await asyncio.shield(spawn_task)
            if limiter is not None:
                limiter.attach(process.pid)
            assert process.stdin is not None and process.stdout is not None
            # The child does not import parsers or touch material until this gate.
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            result = await _read_result(process.stdout)
            return_code = await process.wait()
        try:
            message = json.loads(result)
            if not isinstance(message, dict):
                raise ValueError()
            if message.get("ok") is False:
                error = message.get("error")
                if isinstance(error, str) and 0 < len(error) <= 600:
                    raise ExtractionError(error)
                raise ValueError()
            if return_code or message.get("ok") is not True:
                raise ValueError()
            document = Document.model_validate(message["document"])
            if document.id != item_id:
                raise ValueError()
            return document
        except ExtractionError:
            raise
        except (KeyError, ValueError, RecursionError):
            raise ExtractionError(RESOURCE_ERROR) from None
    except TimeoutError:
        raise ExtractionError("文档解析超过 150 秒，已停止。请拆分材料或重新导出后上传。") from None
    except (OSError, RuntimeError):
        raise ExtractionError(LIMIT_ERROR) from None
    finally:
        late_cancelled = False
        if process is None and spawn_task is not None:
            with contextlib.suppress(Exception):
                process, late_cancelled = await _settle_owned_task(spawn_task)
        if process is not None:
            cleanup = asyncio.create_task(_terminate_worker(process, limiter))
            _, interrupted = await _settle_owned_task(cleanup)
            late_cancelled |= interrupted
        elif limiter is not None:
            limiter.close()
        if late_cancelled:
            raise asyncio.CancelledError


def _worker_main() -> int:
    """The only worker entry point: wait for the parent's gate before extraction."""
    raw = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        return 1
    try:
        if not WINDOWS:
            _unix_limits()
    except (OSError, ValueError):
        message = {"ok": False, "error": LIMIT_ERROR}
    else:
        try:
            request = json.loads(raw)
            from .ingestion import IngestionError, extract_document

            try:
                document = extract_document(Path(request["path"]), Path(request["output"]),
                                            request["item_id"], request["name"])
                message = {"ok": True, "document": document.model_dump()}
            except IngestionError as error:
                # These are the extractor's authored messages, not native/parser
                # exceptions. No traceback or stderr is sent back to the browser.
                message = {"ok": False, "error": str(error)[:600]}
        except Exception:
            message = {"ok": False, "error": RESOURCE_ERROR}
    try:
        encoded = json.dumps(message, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_RESULT_BYTES:
            encoded = json.dumps({"ok": False, "error": RESOURCE_ERROR}, ensure_ascii=False).encode("utf-8")
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
        return 0 if message.get("ok") else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(_worker_main())
