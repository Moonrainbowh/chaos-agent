from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _cleanup(process: subprocess.Popen, job: object | None) -> None:
    if job is not None:
        job.terminate()
        deadline = time.monotonic() + 5
        while job.active_processes():
            if time.monotonic() >= deadline:
                raise RuntimeError("suite Job cleanup did not complete")
            time.sleep(0.01)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def _timeout_record(suite: Path, progress: Path, timeout: float, cleanup: str) -> None:
    from scripts.run_test_suite import _escape_workflow_data

    last_test = "unavailable (discovery or fixture may be running)"
    try:
        value = json.loads(progress.read_text(encoding="utf-8"))["last_test"]
        if isinstance(value, str) and len(value) <= 240 and all(
            part.isidentifier() and part.isascii() for part in value.split(".")
        ):
            last_test = value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    message = (f"suite={suite.as_posix()}; exit_code=124; timeout_seconds={timeout}; "
               f"tests=timeout:{last_test}; cleanup={cleanup}")
    print(message, file=sys.stderr, flush=True)
    if os.environ.get("GITHUB_ACTIONS", "").casefold() == "true":
        print("::error title=Chaos Agent structured test failure::" +
              _escape_workflow_data(message), flush=True)


def run_supervised_suite(root: Path, suite: Path, timeout: float) -> subprocess.CompletedProcess:
    """Gate suite discovery until Job assignment; bound execution and cleanup."""
    job = None
    if os.name == "nt":
        from code_agent.runtime._windows_job import WindowsJob
        job = WindowsJob.create()
    try:
        with tempfile.TemporaryDirectory(prefix="chaos-suite-") as temporary:
            progress = Path(temporary) / "progress.json"
            env = dict(os.environ, CHAOS_TEST_PROGRESS=str(progress), PYTHONUNBUFFERED="1")
            command = (sys.executable, "-m", "scripts.run_test_suite", "--start-dir",
                       str(suite), "--supervised", "--timeout", str(timeout))
            process = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.PIPE,
                                       start_new_session=os.name != "nt")
            return _wait_suite(process, job, command, suite, progress, timeout)
    finally:
        if job is not None:
            job.close()


def _wait_suite(process, job, command, suite, progress, timeout):
    timed_out = False
    cleanup = "confirmed"
    try:
        if job is not None:
            job.assign(process.pid)
        process.stdin.write(b"1")
        process.stdin.flush()
        process.stdin.close()
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = 124
        # The deadline already failed; grace only lets the watchdog flush stacks.
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    finally:
        try:
            if job is not None and not job.assigned:
                process.kill()
                process.wait(timeout=5)
            else:
                _cleanup(process, job)
        except Exception:
            cleanup = "failed"
            raise
        finally:
            if timed_out:
                _timeout_record(suite, progress, timeout, cleanup)
    return subprocess.CompletedProcess(command, returncode)
