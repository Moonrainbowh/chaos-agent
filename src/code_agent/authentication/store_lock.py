from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from .models import AuthError

_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


class StoreLock:
    """Serialize credential read/refresh/write and logout across processes."""

    def __init__(self, path: Path) -> None:
        self.path = path.with_name(path.name + ".lock")
        with _GUARD:
            self.local = _LOCKS.setdefault(str(self.path.resolve()).casefold(), threading.Lock())
        self.handle = None

    def acquire(self) -> None:
        if not self.local.acquire(timeout=90):
            raise AuthError("Credential store is busy; retry after login completes")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            self.handle = os.fdopen(fd, "r+b")
            if self.path.stat().st_size == 0:
                self.handle.write(b"0")
                self.handle.flush()
            deadline = time.monotonic() + 90
            while True:
                try:
                    self.handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except OSError:
                    if time.monotonic() >= deadline:
                        raise AuthError("Credential store is busy") from None
                    time.sleep(0.05)
        except BaseException:
            if self.handle:
                self.handle.close()
                self.handle = None
            self.local.release()
            raise

    def release(self) -> None:
        try:
            if self.handle:
                self.handle.close()
                self.handle = None
        finally:
            self.local.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()
