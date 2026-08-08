from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import TypeVar

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

_T = TypeVar("_T")


async def terminate_process_tree(
    process: asyncio.subprocess.Process,
    process_wait: asyncio.Task[int],
    root_identity: ProcessIdentity,
) -> None:
    """Freeze and terminate one process identity and its Windows descendants."""
    if process.returncode is not None:
        return

    failures: list[str] = []
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

    if failures:
        raise ProcessTreeTerminationError(process.pid, failures)


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
