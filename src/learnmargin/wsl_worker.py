"""Trusted standard-library bridge; document parsing happens only in bubblewrap.

The Windows launcher injects this source and linux_sandbox.py into Python modules,
then calls main(linux_sandbox). No application package is installed in WSL.
stdin: uint32 BE JSON length, JSON v1 header, exact material bytes, then H pulses.
stdout: only a completed PDF. Process status distinguishes every failure class.
"""
from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import stat
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024
MAX_HEADER = 4096
HEARTBEAT_SECONDS = 3.0
INITIAL_SECONDS = 15.0
SUFFIXES = {".doc", ".ppt", ".rtf", ".odt", ".odp"}


class ProtocolError(ValueError):
    pass


class Disconnected(ValueError):
    pass


class DeadlineExpired(ValueError):
    pass


def _header(data: bytes) -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise ProtocolError from None
    if (not isinstance(value, dict) or set(value) != {"version", "suffix", "length", "timeout"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["suffix"], str) or value["suffix"] not in SUFFIXES
            or type(value["length"]) is not int or not 0 < value["length"] <= MAX_BYTES
            or type(value["timeout"]) not in (int, float)
            or not math.isfinite(value["timeout"]) or not 0 < value["timeout"] <= 150):
        raise ProtocolError
    return value


def _read_exact(fd: int, size: int, deadline: float) -> bytes:
    result = bytearray()
    last_progress = time.monotonic()
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while len(result) < size:
            now = time.monotonic()
            if now >= deadline:
                raise DeadlineExpired
            if now - last_progress >= HEARTBEAT_SECONDS:
                raise Disconnected
            if not selector.select(min(0.2, deadline - now)):
                continue
            chunk = os.read(fd, min(65536, size - len(result)))
            if not chunk:
                raise Disconnected
            result.extend(chunk)
            last_progress = time.monotonic()
    return bytes(result)


class _Heartbeat:
    """EOF or missing pulses cancels even when Windows kills only wsl.exe."""

    def __init__(self, fd: int, deadline: float):
        self.fd = fd
        self.deadline = deadline
        self.lost = threading.Event()
        self.invalid = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)

    def _watch(self):
        last_pulse = time.monotonic()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.fd, selectors.EVENT_READ)
                while not self.stopped.is_set():
                    if time.monotonic() - last_pulse >= HEARTBEAT_SECONDS:
                        self.lost.set()
                        return
                    if not selector.select(0.1):
                        continue
                    # Read all queued pulses at once; never count stale bytes as
                    # a new pulse on a later one-second tick.
                    pulse = os.read(self.fd, 65536)
                    if not pulse:
                        self.lost.set()
                        return
                    if pulse.strip(b"H"):
                        self.invalid.set()
                        return
                    last_pulse = time.monotonic()
        except OSError:
            self.lost.set()

    def cancelled(self) -> bool:
        return self.lost.is_set() or self.invalid.is_set() or time.monotonic() >= self.deadline

    def check(self):
        if self.invalid.is_set():
            raise ProtocolError
        if self.lost.is_set():
            raise Disconnected
        if time.monotonic() >= self.deadline:
            raise DeadlineExpired

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopped.set()
        self.thread.join(timeout=1)


def _tmpfs() -> Path:
    directory = Path("/dev/shm")
    if directory.is_symlink() or not directory.is_dir():
        raise ProtocolError
    # This is trusted OS metadata, never material XML/archive data.
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, separator, after = line.partition(" - ")
        fields = before.split()
        if separator and len(fields) > 4 and fields[4] == "/dev/shm" and after.split()[0] == "tmpfs":
            return directory
    raise ProtocolError


def _dedicated_environment() -> bool:
    if sys.platform != "linux" or os.getuid() == 0:
        return False
    handle = os.open("/etc/learnmargin-office-release", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(handle)
        return (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and info.st_nlink == 1 and info.st_size == len(b"LearnMargin-Office-v1\n")
                and os.read(handle, 64) == b"LearnMargin-Office-v1\n")
    finally:
        os.close(handle)


def _send_pdf(fd: int, path: Path, heartbeat: _Heartbeat):
    handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 5 <= info.st_size <= MAX_BYTES:
            raise ProtocolError
        first = os.read(handle, 65536)
        if not first.startswith(b"%PDF-"):
            raise ProtocolError
        was_blocking = os.get_blocking(fd)
        os.set_blocking(fd, False)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(fd, selectors.EVENT_WRITE)
                chunk, total = first, 0
                while chunk:
                    if total + len(chunk) > MAX_BYTES:
                        raise ProtocolError
                    pending = memoryview(chunk)
                    while pending:
                        heartbeat.check()
                        if not selector.select(0.1):
                            continue
                        try:
                            written = os.write(fd, pending)
                        except BlockingIOError:
                            continue
                        if written <= 0:
                            raise Disconnected
                        pending = pending[written:]
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ProtocolError
                    chunk = os.read(handle, min(65536, MAX_BYTES - total + 1))
                if total != info.st_size:
                    raise ProtocolError
        finally:
            os.set_blocking(fd, was_blocking)
    finally:
        os.close(handle)


def serve(sandbox, *, input_fd: int = 0, output_fd: int = 1) -> int:
    """Return a fixed status. All task files and sandbox children die first."""
    try:
        try:
            available = _dedicated_environment()
        except OSError:
            available = False
        if not available:
            return 3
        initial = time.monotonic() + INITIAL_SECONDS
        size = struct.unpack("!I", _read_exact(input_fd, 4, initial))[0]
        if not 0 < size <= MAX_HEADER:
            raise ProtocolError
        request = _header(_read_exact(input_fd, size, initial))
        deadline = time.monotonic() + request["timeout"]
        executable = shutil.which("libreoffice") or shutil.which("soffice")
        if not executable:
            return 3
        with tempfile.TemporaryDirectory(prefix="learnmargin-wsl-", dir=_tmpfs()) as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source, output = root / ("source" + request["suffix"]), root / "result.pdf"
            remaining = request["length"]
            with source.open("xb") as target:
                while remaining:
                    chunk = _read_exact(input_fd, min(65536, remaining), deadline)
                    target.write(chunk)
                    remaining -= len(chunk)
            source.chmod(0o400)
            with _Heartbeat(input_fd, deadline) as heartbeat:
                heartbeat.check()
                try:
                    sandbox.run_linux_conversion(executable, source, output,
                        timeout=max(0.001, deadline - time.monotonic()), cancelled=heartbeat.cancelled)
                except sandbox.SandboxUnavailable:
                    heartbeat.check()
                    return 3
                except sandbox.SandboxConversionError:
                    heartbeat.check()
                    return 4
                heartbeat.check()
                # run_linux_conversion only returns after reaping bubblewrap;
                # its PID namespace and any detached parser descendants are dead.
                _send_pdf(output_fd, output, heartbeat)
        return 0
    except ProtocolError:
        return 2
    except Disconnected:
        return 5
    except DeadlineExpired:
        return 6
    except (OSError, ValueError):
        return 4


def main(sandbox) -> None:
    code = serve(sandbox)
    if code:
        # No OS diagnostics, host paths, document names or content cross back.
        os.write(2, f"LearnMargin WSL conversion failed ({code}).\n".encode("ascii"))
    raise SystemExit(code)
