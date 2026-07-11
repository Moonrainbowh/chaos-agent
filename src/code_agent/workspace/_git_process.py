from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
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


def collect_bounded_output(
    process: subprocess.Popen[bytes],
    max_output_bytes: int,
    timeout_s: float,
) -> ProcessCapture:
    """Drain both pipes concurrently with bounded wait and cleanup times."""
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    lock = threading.Lock()
    stop = threading.Event()
    exceeded = False
    timed_out = False
    read_error: BaseException | None = None
    started_threads: list[threading.Thread] = []

    def read_pipe(stream: BinaryIO, sink: bytearray) -> None:
        nonlocal exceeded, read_error
        try:
            while not stop.is_set():
                chunk = stream.read(min(65_536, max_output_bytes + 1))
                if not chunk:
                    return
                over_limit = False
                with lock:
                    remaining = max_output_bytes - len(stdout) - len(stderr)
                    kept = min(remaining, len(chunk))
                    sink.extend(chunk[:kept])
                    if kept < len(chunk):
                        exceeded = True
                        stop.set()
                        over_limit = True
                if over_limit:
                    _kill_process(process)
                    return
        except BaseException as error:
            with lock:
                if read_error is None and not stop.is_set():
                    read_error = error
                    stop.set()
            _kill_process(process)
        finally:
            _close_pipe(stream)

    threads: list[threading.Thread] = []
    primary_error: BaseException | None = None
    try:
        for stream, sink in (
            (process.stdout, stdout),
            (process.stderr, stderr),
        ):
            threads.append(
                threading.Thread(
                    target=read_pipe, args=(stream, sink), daemon=True
                )
            )
        for thread in threads:
            thread.start()
            started_threads.append(thread)
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop.set()
            _kill_process(process)
    except BaseException as error:
        primary_error = error
        stop.set()
        _kill_process(process)
    finally:
        cleanup_deadline = time.monotonic() + _MAX_CLEANUP_SECONDS
        if stop.is_set() or primary_error is not None:
            _kill_process(process)
            _bounded_wait(process, cleanup_deadline)
            _close_pipe(process.stdout)
            _close_pipe(process.stderr)
        for thread in started_threads:
            remaining = max(0.0, cleanup_deadline - time.monotonic())
            thread.join(timeout=remaining)
        _close_pipe(process.stdout)
        _close_pipe(process.stderr)

    if primary_error is not None:
        raise primary_error
    return ProcessCapture(
        bytes(stdout),
        bytes(stderr),
        process.returncode,
        exceeded,
        timed_out,
        read_error,
    )


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
