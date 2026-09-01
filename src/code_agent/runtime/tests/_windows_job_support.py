from __future__ import annotations

import asyncio
from pathlib import Path


class FakeJobApi:
    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.events: list[tuple[object, ...]] = []

    def create_job(self) -> int:
        self.events.append(("create_job",))
        return 100

    def set_kill_on_close(self, handle: int) -> None:
        self.events.append(("set_kill_on_close", handle))
        if self.failure == "configure":
            raise OSError(5, "configuration denied")

    def open_process(self, pid: int) -> int:
        self.events.append(("open_process", pid))
        return 200

    def assign_process(self, job_handle: int, process_handle: int) -> None:
        self.events.append(("assign_process", job_handle, process_handle))
        if self.failure == "assign":
            raise OSError(5, "process is already in a non-breakaway job")

    def terminate_job(self, handle: int, exit_code: int) -> None:
        self.events.append(("terminate_job", handle, exit_code))

    def active_processes(self, handle: int) -> int:
        self.events.append(("active_processes", handle))
        return 0

    def close_handle(self, handle: int) -> None:
        self.events.append(("close_handle", handle))


class FakeReader:
    def __init__(self, chunks: tuple[bytes, ...] = ()) -> None:
        self._chunks = list(chunks)
        self._eof = asyncio.Event()

    async def read(self, size: int = -1) -> bytes:
        del size
        if self._chunks:
            return self._chunks.pop(0)
        await self._eof.wait()
        return b""

    def feed_eof(self) -> None:
        self._eof.set()


class FakeProcess:
    def __init__(self, stdout: tuple[bytes, ...] = ()) -> None:
        self.pid = 8123
        self.returncode: int | None = None
        self.stdout = FakeReader(stdout)
        self.stderr = FakeReader()
        self._done = asyncio.Event()

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode

    def complete(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self._done.set()
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    def kill(self) -> None:
        self.complete(-9)


class FakeLease:
    def __init__(self, path: Path, guard: object) -> None:
        del guard
        self.path = path

    def __enter__(self) -> "FakeLease":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


class RecordingJob:
    def __init__(self, events: list[str], process: FakeProcess | None = None) -> None:
        self.events = events
        self.process = process
        self.closed = False
        self.terminate_calls = 0
        self.assign_error: BaseException | None = None

    def assign(self, pid: int) -> None:
        self.events.append(f"assign:{pid}")
        if self.assign_error is not None:
            raise self.assign_error

    def terminate(self, exit_code: int = 1) -> None:
        self.events.append(f"terminate:{exit_code}")
        self.terminate_calls += 1
        if self.process is not None:
            self.process.complete(exit_code)

    def active_processes(self) -> int:
        if self.process is None or self.process.returncode is not None:
            return 0
        return 1

    def close(self) -> None:
        self.events.append("close")
        self.closed = True
