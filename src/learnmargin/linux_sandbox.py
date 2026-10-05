"""Fail-closed Linux document conversion in a deliberately small filesystem.

Only distro-managed LibreOffice installations under /usr/lib[64] are supported.
The converter receives no writable host mount: its bounded tmpfs output is
returned through a bounded pipe. Bubblewrap supplies the OS security boundary;
the surrounding extraction worker supplies additional resource limits.
"""
from __future__ import annotations

import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAX_PDF_BYTES = 50 * 1024 * 1024
TMP_BYTES = 128 * 1024 * 1024
WORK_BYTES = 256 * 1024 * 1024
HOME_BYTES = 32 * 1024 * 1024


class SandboxUnavailable(ValueError):
    """The required OS isolation could not be established."""


class SandboxConversionError(ValueError):
    """A sandboxed document did not produce a bounded PDF."""


@dataclass(frozen=True)
class LinuxRuntime:
    bwrap: str
    python: str
    office: str | None
    mounts: tuple[tuple[str, str], ...]


def _runtime(executable: str | None, *, require_office: bool = True) -> LinuxRuntime:
    if sys.platform != "linux":
        raise SandboxUnavailable("此转换沙箱需要 Linux、bubblewrap 和系统 LibreOffice。")
    bwrap = shutil.which("bwrap")
    python = Path("/usr/bin/python3")
    if not bwrap or not python.is_file():
        raise SandboxUnavailable("缺少 bubblewrap 或系统 Python，无法隔离旧格式转换。")
    office = None
    if executable:
        candidate = Path(executable).resolve().with_name("soffice.bin")
        allowed = {Path("/usr/lib/libreoffice/program/soffice.bin"),
                   Path("/usr/lib64/libreoffice/program/soffice.bin")}
        if candidate not in allowed or not candidate.is_file():
            raise SandboxUnavailable("转换沙箱需要安装在 /usr/lib 或 /usr/lib64 的系统 LibreOffice。")
        office = str(candidate)
    elif require_office:
        raise SandboxUnavailable("未找到可在沙箱中运行的 LibreOffice。")

    # Do not bind /, /usr as a whole, /etc as a whole, /home, /run, /mnt,
    # /media, /opt or /usr/local. Those can contain credentials or user data.
    paths = ("/usr/lib", "/usr/lib64", "/lib", "/lib64",
             "/usr/share/libreoffice", "/usr/share/fonts", "/usr/share/fontconfig",
             "/usr/share/locale", "/usr/share/icu", "/etc/fonts",
             "/etc/ld.so.cache", "/etc/localtime", "/etc/libreoffice/sofficerc")
    mounts = [(str(Path(path).resolve()), path) for path in paths if Path(path).exists()]
    # Debian/Ubuntu's public registry entry is a symlink through /etc. Supply
    # only the distro's defaults; host /etc/libreoffice/registry may contain
    # local LDAP configuration and must not enter the document sandbox.
    for base in ("/usr/lib/libreoffice", "/usr/lib64/libreoffice"):
        registry = Path(base) / "share" / ".registry"
        if registry.is_dir():
            mounts.append((str(registry.resolve()), "/etc/libreoffice/registry"))
            break
    mounts.append((str(python.resolve()), "/usr/bin/python3"))
    return LinuxRuntime(bwrap, "/usr/bin/python3", office, tuple(mounts))


def _sandbox_command(runtime: LinuxRuntime, command: list[str],
                     input_file: Path | None = None) -> list[str]:
    # All required namespaces are explicit: *-try / --share-net are forbidden.
    # --disable-userns needs bubblewrap >= 0.9. Unknown options fail closed.
    arguments = [runtime.bwrap, "--unshare-user", "--unshare-pid", "--unshare-net",
                 "--unshare-ipc", "--unshare-uts", "--disable-userns",
                 "--assert-userns-disabled", "--cap-drop", "ALL", "--new-session",
                 "--die-with-parent", "--clearenv"]
    for source, destination in runtime.mounts:
        arguments += ["--ro-bind", source, destination]
    arguments += ["--proc", "/proc", "--remount-ro", "/proc", "--dev", "/dev",
                  "--size", "16777216", "--tmpfs", "/dev/shm",
                  "--size", str(TMP_BYTES), "--tmpfs", "/tmp",
                  "--size", str(WORK_BYTES), "--tmpfs", "/work",
                  "--size", str(HOME_BYTES), "--tmpfs", "/home",
                  "--size", "1048576", "--tmpfs", "/run",
                  "--dir", "/input", "--chdir", "/work"]
    if input_file is not None:
        arguments += ["--ro-bind", str(input_file), f"/input/source{input_file.suffix.lower()}"]
    for name, value in {"PATH": "/usr/bin", "HOME": "/home", "TMPDIR": "/tmp",
                        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "SAL_USE_VCLPLUGIN": "svp",
                        "XDG_CACHE_HOME": "/home/cache", "XDG_CONFIG_HOME": "/home/config",
                        "XDG_RUNTIME_DIR": "/run"}.items():
        arguments += ["--setenv", name, value]
    # No writes to root or synthetic /usr, /etc, /input directories either.
    return arguments + ["--remount-ro", "/", "--", *command]


# This trusted driver runs INSIDE the same sandbox as the untrusted parser.
# A compromised converter can corrupt only sandbox files/stdout. The parent
# independently bounds and checks that output before writing any host file.
_CONVERT = r'''
import os, pathlib, resource, stat, subprocess, sys
limit = 50 * 1024 * 1024
resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
resource.setrlimit(resource.RLIMIT_CPU, (110, 110))
resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
profile = pathlib.Path('/home/profile/user')
profile.mkdir(parents=True)
(profile / 'registrymodifications.xcu').write_text(
    '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
    '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
    '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
    '<item oor:path="/org.openoffice.Office.Common/Load">'
    '<prop oor:name="Update" oor:op="fuse"><value>0</value></prop></item></oor:items>',
    encoding='utf-8')
command = [sys.argv[1], '-env:UserInstallation=file:///home/profile',
    '--headless', '--nologo', '--nodefault', '--nolockcheck', '--norestore',
    '--convert-to', 'pdf', '--outdir', '/work', sys.argv[2]]
# soffice.bin can request one normal restart after profile initialization.
# Preserve the same namespace/profile and the parent's single time budget.
for attempt in range(2):
    completed = subprocess.run(command, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if completed.returncode != 81:
        break
if completed.returncode:
    print('LibreOffice exit code:', completed.returncode, file=sys.stderr)
    sys.exit(2)
fd = os.open('/work/source.pdf', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
with os.fdopen(fd, 'rb') as result:
    info = os.fstat(result.fileno())
    if not stat.S_ISREG(info.st_mode) or not 5 <= info.st_size <= limit:
        sys.exit(3)
    while chunk := result.read(65536):
        sys.stdout.buffer.write(chunk)
'''


def _terminate(process: subprocess.Popen) -> None:
    # With its own PID namespace, killing bubblewrap's reaper kills even
    # descendants that attempted to detach into another process session.
    if process.poll() is not None:
        return
    if os.environ.get("LEARNMARGIN_EXTRACTION_WORKER") != "1":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def _run(command: list[str], timeout: float, *, limit: int,
         cancelled: Callable[[], bool] | None = None) -> bytes:
    deadline = time.monotonic() + timeout
    output = bytearray()
    # Never inherit LD_PRELOAD, PYTHONPATH, credentials or open descriptors.
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=environment, close_fds=True,
            start_new_session=os.environ.get("LEARNMARGIN_EXTRACTION_WORKER") != "1",
        )
    except OSError:
        raise SandboxUnavailable("无法启动隔离转换；请检查 bubblewrap 和系统沙箱支持。") from None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if cancelled is not None and cancelled():
                    raise SandboxConversionError("隔离转换已取消。")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxConversionError("隔离转换超时，请拆分材料或先导出 PDF。")
                for key, _ in selector.select(min(remaining, 0.2) if cancelled else remaining):
                    chunk = os.read(key.fileobj.fileno(), min(65536, limit - len(output) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > limit:
                            raise SandboxConversionError("转换后的 PDF 超过 50 MB，请拆分材料。")
            while process.poll() is None:
                if cancelled is not None and cancelled():
                    raise SandboxConversionError("隔离转换已取消。")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxConversionError("隔离转换超时，请拆分材料或先导出 PDF。")
                try:
                    process.wait(timeout=min(remaining, 0.2) if cancelled else remaining)
                except subprocess.TimeoutExpired:
                    continue
            result = process.returncode
            if result:
                raise SandboxConversionError("隔离转换失败；请检查沙箱支持，或用原应用导出 PDF。")
        return bytes(output)
    finally:
        _terminate(process)
        if process.stdout:
            process.stdout.close()


def linux_sandbox_available(executable: str | None = None) -> bool:
    """Probe required OS protections; never substitute an ordinary process."""
    try:
        runtime = _runtime(executable)
        command = _sandbox_command(runtime, [runtime.python, "-I", "-S", "-c", "print('isolated')"])
        return _run(command, 5, limit=64) == b"isolated\n"
    except (SandboxUnavailable, SandboxConversionError, OSError):
        return False


def static_linux_support(executable: str | None = None) -> bool:
    """Check installed prerequisites without launching a process in settings.

    The real conversion still establishes every namespace and fails closed if
    the installed bubblewrap is too old or the OS prohibits user namespaces.
    """
    try:
        _runtime(executable)
        return True
    except (SandboxUnavailable, OSError):
        return False


def run_linux_conversion(executable: str, input_file: Path, output_file: Path,
                         *, timeout: float = 120,
                         cancelled: Callable[[], bool] | None = None) -> None:
    """Write one PDF after successful isolation, bounded conversion and output."""
    input_file, output_file = Path(input_file), Path(output_file)
    if (input_file.is_symlink() or not input_file.is_file()
            or input_file.suffix.lower() not in {".doc", ".ppt", ".rtf", ".odt", ".odp"}
            or not 0 < input_file.stat().st_size <= MAX_PDF_BYTES):
        raise SandboxConversionError("转换材料路径或大小无效，请重新上传。")
    runtime = _runtime(executable)
    command = _sandbox_command(runtime, [runtime.python, "-I", "-S", "-c", _CONVERT,
                                       runtime.office, f"/input/source{input_file.suffix.lower()}"],
                               input_file.resolve())
    options = {"cancelled": cancelled} if cancelled is not None else {}
    data = _run(command, timeout, limit=MAX_PDF_BYTES, **options)
    if not data.startswith(b"%PDF-"):
        raise SandboxConversionError("LibreOffice 未能生成有效 PDF，请用原应用重新导出。")
    # The caller owns a new work directory. Exclusive creation also prevents
    # accidental overwrite or following an existing output symlink.
    with output_file.open("xb") as stream:
        stream.write(data)
