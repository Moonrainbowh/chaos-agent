from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import BinaryIO


_MAX_CLEANUP_SECONDS = 1.0


@dataclass(frozen=True)
class ProcessCapture:
    stdout: bytes
    stderr: bytes
    returncode: int | None
    exceeded: bool
    timed_out: bool
    read_error: BaseException | None


@dataclass
class _CaptureState:
    max_output_bytes: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    exceeded: bool = False
    timed_out: bool = False
    read_error: BaseException | None = None

    def keep_chunk(self, sink: bytearray, chunk: bytes) -> bool:
        with self.lock:
            remaining = (
                self.max_output_bytes - len(self.stdout) - len(self.stderr)
            )
            kept = min(remaining, len(chunk))
            sink.extend(chunk[:kept])
            if kept == len(chunk):
                return False
            self.exceeded = True
            self.stop.set()
            return True

    def record_read_error(self, error: BaseException) -> None:
        with self.lock:
            if self.read_error is None and not self.stop.is_set():
                self.read_error = error
                self.stop.set()

    def capture(self, returncode: int | None) -> ProcessCapture:
        return ProcessCapture(
            bytes(self.stdout),
            bytes(self.stderr),
            returncode,
            self.exceeded,
            self.timed_out,
            self.read_error,
        )


def collect_bounded_output(
    process: subprocess.Popen[bytes],
    max_output_bytes: int,
    timeout_s: float,
) -> ProcessCapture:
    """Drain both pipes concurrently with bounded wait and cleanup times."""
    assert process.stdout is not None and process.stderr is not None
    state = _CaptureState(max_output_bytes)
    started_threads: list[threading.Thread] = []
    primary_error: BaseException | None = None
    try:
        threads = _build_reader_threads(process, state)
        _start_reader_threads(threads, started_threads)
        _wait_for_process(process, state, timeout_s)
    except BaseException as error:
        primary_error = error
        state.stop.set()
        _kill_process(process)
    finally:
        _cleanup_capture(process, state, started_threads, primary_error)

    if primary_error is not None:
        raise primary_error
    return state.capture(process.returncode)


def _read_pipe(
    process: subprocess.Popen[bytes],
    state: _CaptureState,
    stream: BinaryIO,
    sink: bytearray,
) -> None:
    try:
        while not state.stop.is_set():
            chunk = stream.read(min(65_536, state.max_output_bytes + 1))
            if not chunk:
                return
            if state.keep_chunk(sink, chunk):
                _kill_process(process)
                return
    except BaseException as error:
        state.record_read_error(error)
        _kill_process(process)
    finally:
        _close_pipe(stream)


def _build_reader_threads(
    process: subprocess.Popen[bytes],
    state: _CaptureState,
) -> list[threading.Thread]:
    assert process.stdout is not None and process.stderr is not None
    threads: list[threading.Thread] = []
    for stream, sink in (
        (process.stdout, state.stdout),
        (process.stderr, state.stderr),
    ):
        threads.append(
            threading.Thread(
                target=_read_pipe,
                args=(process, state, stream, sink),
                daemon=True,
            )
        )
    return threads


def _start_reader_threads(
    threads: list[threading.Thread],
    started_threads: list[threading.Thread],
) -> None:
    for thread in threads:
        thread.start()
        started_threads.append(thread)


def _wait_for_process(
    process: subprocess.Popen[bytes],
    state: _CaptureState,
    timeout_s: float,
) -> None:
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        state.timed_out = True
        state.stop.set()
        _kill_process(process)


def _cleanup_capture(
    process: subprocess.Popen[bytes],
    state: _CaptureState,
    started_threads: list[threading.Thread],
    primary_error: BaseException | None,
) -> None:
    cleanup_deadline = time.monotonic() + _MAX_CLEANUP_SECONDS
    if state.stop.is_set() or primary_error is not None:
        _kill_process(process)
        _bounded_wait(process, cleanup_deadline)
        _close_process_pipes(process)
    _join_threads(started_threads, cleanup_deadline)
    _close_process_pipes(process)


def _join_threads(
    threads: list[threading.Thread],
    deadline: float,
) -> None:
    for thread in threads:
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(timeout=remaining)


def _close_process_pipes(process: subprocess.Popen[bytes]) -> None:
    assert process.stdout is not None and process.stderr is not None
    _close_pipe(process.stdout)
    _close_pipe(process.stderr)


def _bounded_wait(process: subprocess.Popen[bytes], deadline: float) -> None:
    remaining = max(0.0, deadline - time.monotonic())
    try:
        process.wait(timeout=remaining)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.kill()
    except OSError:
        pass


def _close_pipe(stream: BinaryIO) -> None:
    try:
        stream.close()
    except OSError:
        pass
