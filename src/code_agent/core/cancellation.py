from __future__ import annotations

import asyncio
from threading import Event, Lock
from typing import Optional


_DEFAULT_REASON = "cancelled"


class CancellationError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._reason: Optional[str] = None
        self._waiters: set[asyncio.Future[bool]] = set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> Optional[str]:
        with self._lock:
            return self._reason

    def cancel(self, reason: Optional[str] = None) -> bool:
        if reason is None:
            reason = _DEFAULT_REASON
        elif not isinstance(reason, str):
            raise TypeError("reason must be a string or None")
        if not reason.strip():
            raise ValueError("reason must not be blank")

        with self._lock:
            if self._event.is_set():
                return False
            self._reason = reason
            self._event.set()
            waiters = tuple(self._waiters)
            self._waiters.clear()

        for waiter in waiters:
            loop = waiter.get_loop()
            try:
                loop.call_soon_threadsafe(self._wake_waiter, waiter)
            except RuntimeError:
                pass
        return True

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout)

    async def wait_async(self, timeout: Optional[float] = None) -> bool:
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[bool] = loop.create_future()
        with self._lock:
            if self._event.is_set():
                return True
            self._waiters.add(waiter)

        try:
            if timeout is None:
                return await waiter
            try:
                return await asyncio.wait_for(waiter, timeout)
            except asyncio.TimeoutError:
                return False
        finally:
            with self._lock:
                self._waiters.discard(waiter)
            if not waiter.done():
                waiter.cancel()

    @staticmethod
    def _wake_waiter(waiter: asyncio.Future[bool]) -> None:
        if not waiter.done():
            waiter.set_result(True)

    def raise_if_cancelled(self) -> None:
        with self._lock:
            if not self._event.is_set():
                return
            reason = self._reason
        if reason is None:
            raise RuntimeError("cancelled token has no reason")
        raise CancellationError(reason)
