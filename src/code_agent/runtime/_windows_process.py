from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterable
from typing import Any, TypeVar

import psutil

from ._process_snapshot import (
    ProcessIdentity,
    capture_process_identity,
    freeze_process_tree,
    kill_and_wait_descendants,
)
from .errors import ProcessTreeTerminationError


_MAX_PROCESS_TREE_SIZE = 1024
_THREAD_CALL_TIMEOUT_S = 2.0
_ROOT_WAIT_TIMEOUT_S = 1.0
_TASK_CLEANUP_TIMEOUT_S = 1.0
_JOB_EMPTY_TIMEOUT_S = 1.0
_JOB_POLL_INTERVAL_S = 0.01

_T = TypeVar("_T")


async def terminate_process_tree(
    process: asyncio.subprocess.Process,
    process_wait: asyncio.Task[int],
    root_identity: ProcessIdentity,
    job: Any | None = None,
) -> None:
    """Terminate the owned Job, with identity-bound psutil cleanup as fallback."""
    failures: list[str] = []
    if job is not None:
        should_terminate = process.returncode is None
        if not should_terminate:
            active = _job_active_processes(job, failures)
            should_terminate = active is None or active > 0
            if active == 0:
                return
        if should_terminate:
            try:
                job.terminate()
            except BaseException as error:
                failures.append(
                    f"TerminateJobObject failed: {type(error).__name__}: {error}"
                )
            else:
                if await _wait_for_empty_job(job, failures):
                    if process.returncode is not None or await _bounded_task_wait(
                        process_wait, _ROOT_WAIT_TIMEOUT_S
                    ):
                        return
                    failures.append(
                        "root process did not exit after Job became empty"
                    )

    await _terminate_with_snapshot(
        process, process_wait, root_identity, failures
    )
    if failures:
        raise ProcessTreeTerminationError(process.pid, failures)


async def complete_process_termination(
    process: asyncio.subprocess.Process,
    process_wait: asyncio.Task[int],
    root_identity: ProcessIdentity,
    job: Any | None,
) -> None:
    """Finish bounded termination even if the caller task is cancelled."""
    task = asyncio.create_task(
        terminate_process_tree(process, process_wait, root_identity, job)
    )
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if task.done():
                try:
                    task.result()
                except BaseException as cleanup_error:
                    raise cleanup_error from error
                raise
            cancellation = error
            continue
        except BaseException as error:
            if cancellation is not None:
                raise error from cancellation
            raise
        break
    if cancellation is not None:
        raise cancellation


def _job_active_processes(job: Any, failures: list[str]) -> int | None:
    try:
        return job.active_processes()
    except BaseException as error:
        failures.append(
            f"QueryInformationJobObject failed: {type(error).__name__}: {error}"
        )
        return None


async def _wait_for_empty_job(job: Any, failures: list[str]) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _JOB_EMPTY_TIMEOUT_S
    while True:
        active = _job_active_processes(job, failures)
        if active is None:
            return False
        if active == 0:
            return True
        if loop.time() >= deadline:
            failures.append(
                f"Job still owns {active} process(es) after termination deadline"
            )
            return False
        await asyncio.sleep(_JOB_POLL_INTERVAL_S)


async def _terminate_with_snapshot(
    process: asyncio.subprocess.Process,
    process_wait: asyncio.Task[int],
    root_identity: ProcessIdentity,
    failures: list[str],
) -> None:
    if process.returncode is not None:
        return

    descendants: tuple[ProcessIdentity, ...] = ()
    try:
        frozen = await _bounded_to_thread(
            lambda: freeze_process_tree(
                root_identity,
                process_api=psutil,
                max_processes=_MAX_PROCESS_TREE_SIZE,
            ),
            timeout=_THREAD_CALL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        failures.append("process-tree discovery thread deadline exceeded")
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied:
        failures.append(f"access denied while discovering root PID {process.pid}")
    except BaseException as error:
        failures.append(
            f"process-tree discovery failed: {type(error).__name__}: {error}"
        )
    else:
        descendants = frozen.descendants
        failures.extend(frozen.failures)

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except OSError as error:
            failures.append(
                f"direct root kill failed: {type(error).__name__}: {error}"
            )

    if descendants:
        try:
            descendant_failures = await _bounded_to_thread(
                lambda: kill_and_wait_descendants(
                    descendants,
                    process_api=psutil,
                ),
                timeout=_THREAD_CALL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            failures.append("descendant cleanup thread deadline exceeded")
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            failures.append("access denied while cleaning descendant processes")
        except BaseException as error:
            failures.append(
                f"descendant cleanup failed: {type(error).__name__}: {error}"
            )
        else:
            failures.extend(descendant_failures)

    # Descendants may inherit the root's stdout/stderr handles. On Windows,
    # awaiting the root before closing those inherited handles can keep the
    # asyncio subprocess transport open until the descendants exit.
    if process.returncode is None and not await _bounded_task_wait(
        process_wait, _ROOT_WAIT_TIMEOUT_S
    ):
        failures.append("root process did not exit before deadline")



def close_process_job(job: Any, pid: int) -> None:
    """Close the command's Job before waiting for inherited pipe handles."""
    try:
        job.close()
    except BaseException as error:
        raise ProcessTreeTerminationError(
            pid,
            (f"CloseHandle(job) failed: {type(error).__name__}: {error}",),
        ) from error


async def finish_process_tasks(
    job: Any,
    pid: int,
    tasks: Iterable[asyncio.Future[object]],
) -> None:
    """Close a remaining Job and always consume all monitor/pipe tasks."""
    prior_error = sys.exc_info()[1]
    close_error: ProcessTreeTerminationError | None = None
    if not job.closed:
        try:
            close_process_job(job, pid)
        except ProcessTreeTerminationError as error:
            close_error = error
    tasks = tuple(tasks)
    for task in tasks:
        if not task.done():
            task.cancel()
    await finish_tasks(tasks)
    if close_error is not None:
        if isinstance(prior_error, ProcessTreeTerminationError):
            raise ProcessTreeTerminationError(
                pid, (*prior_error.failures, *close_error.failures)
            ) from prior_error
        if prior_error is not None:
            raise ProcessTreeTerminationError(
                pid,
                (
                    f"prior runtime failure: {type(prior_error).__name__}",
                    *close_error.failures,
                ),
            ) from prior_error
        raise close_error


async def require_output_tasks(
    tasks: Iterable[asyncio.Task[None]],
    pid: int,
) -> None:
    """Require both pipe readers to reach EOF before returning success."""
    tasks = tuple(tasks)
    done, pending = await asyncio.wait(
        tasks, timeout=_TASK_CLEANUP_TIMEOUT_S
    )
    failures: list[str] = []
    if pending:
        failures.append("output pipe readers did not reach EOF before deadline")
    for task in done:
        if task.cancelled():
            failures.append("output pipe reader was cancelled")
        elif task.exception() is not None:
            error = task.exception()
            failures.append(
                f"output pipe reader failed: {type(error).__name__}: {error}"
            )
    if failures:
        raise ProcessTreeTerminationError(pid, failures)


async def finish_tasks(tasks: Iterable[asyncio.Future[object]]) -> None:
    """Wait briefly for tasks and consume completed task exceptions."""
    tasks = tuple(tasks)
    pending = tuple(task for task in tasks if not task.done())
    if pending:
        await asyncio.wait(pending, timeout=_TASK_CLEANUP_TIMEOUT_S)
    for task in tasks:
        if task.done() and not task.cancelled():
            task.exception()


async def _bounded_task_wait(
    task: asyncio.Future[object], timeout: float
) -> bool:
    done, _ = await asyncio.wait((task,), timeout=timeout)
    return bool(done)


async def _bounded_to_thread(
    function: Callable[[], _T], *, timeout: float
) -> _T:
    return await asyncio.wait_for(asyncio.to_thread(function), timeout=timeout)
