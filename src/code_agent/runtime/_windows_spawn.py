from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from ._process_snapshot import ProcessIdentity
from ._windows_process import terminate_process_tree
from .errors import ProcessTreeTerminationError, RuntimeStartError


_STARTUP_CLEANUP_TIMEOUT_S = 1.0


async def spawn_suspended_process(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    creationflags: int,
    guard: Any,
    lease_factory: Callable[..., Any],
    create_process: Callable[..., Awaitable[asyncio.subprocess.Process]],
    capture_identity: Callable[[int, Any], ProcessIdentity],
    resume_identity: Callable[[ProcessIdentity, Any], None],
    process_api: Any,
) -> tuple[asyncio.subprocess.Process, ProcessIdentity]:
    with lease_factory(cwd, guard) as lease:
        process = await create_process(
            *argv,
            cwd=str(lease.path),
            env=dict(environment),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=False,
            creationflags=creationflags,
        )
        identity: ProcessIdentity | None = None
        try:
            identity = capture_identity(process.pid, process_api)
            resume_identity(identity, process_api)
        except BaseException as setup_error:
            try:
                await _cleanup_failed_start(process, identity)
            except ProcessTreeTerminationError as cleanup_error:
                raise cleanup_error from setup_error
            raise RuntimeStartError(
                "failed to establish suspended process identity"
            ) from setup_error
    return process, identity


async def _cleanup_failed_start(
    process: asyncio.subprocess.Process,
    identity: ProcessIdentity | None,
) -> None:
    process_wait = asyncio.create_task(process.wait())
    pipe_tasks = (
        ("stdout pipe cleanup", asyncio.create_task(_drain(process.stdout))),
        ("stderr pipe cleanup", asyncio.create_task(_drain(process.stderr))),
    )
    tasks = (process_wait, *(task for _, task in pipe_tasks))
    failures: list[str] = []
    tree_error: ProcessTreeTerminationError | None = None
    try:
        if identity is None:
            _kill_suspended_root(process, failures)
        else:
            try:
                await terminate_process_tree(process, process_wait, identity)
            except ProcessTreeTerminationError as error:
                tree_error = error
                failures.extend(error.failures)
            except BaseException as error:
                failures.append(
                    f"process tree cleanup failed: {type(error).__name__}: {error}"
                )

        await _collect_task_failure(process_wait, "root wait cleanup", failures)
        if (
            process_wait.done()
            and not process_wait.cancelled()
            and process_wait.exception() is None
            and process.returncode is None
        ):
            failures.append("root wait cleanup completed without returncode")
        for label, task in pipe_tasks:
            await _collect_task_failure(task, label, failures)
    finally:
        await _cancel_and_consume(tasks)

    if failures:
        normalized = tuple(dict.fromkeys(failures))
        if tree_error is not None and normalized == tree_error.failures:
            raise tree_error
        raise ProcessTreeTerminationError(process.pid, normalized)


def _kill_suspended_root(
    process: asyncio.subprocess.Process,
    failures: list[str],
) -> None:
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:
        pass
    except OSError as error:
        failures.append(f"root kill failed: {type(error).__name__}: {error}")


async def _drain(reader: asyncio.StreamReader | None) -> None:
    if reader is not None:
        await reader.read()


async def _collect_task_failure(
    task: asyncio.Task[Any],
    label: str,
    failures: list[str],
) -> None:
    done, _ = await asyncio.wait(
        (task,), timeout=_STARTUP_CLEANUP_TIMEOUT_S
    )
    if not done:
        failures.append(f"{label} deadline exceeded")
        return
    if task.cancelled():
        failures.append(f"{label} was cancelled")
        return
    error = task.exception()
    if error is not None:
        failures.append(f"{label} failed: {type(error).__name__}: {error}")


async def _cancel_and_consume(tasks: tuple[asyncio.Task[Any], ...]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
