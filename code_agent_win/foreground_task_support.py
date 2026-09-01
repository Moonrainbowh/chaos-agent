from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from pathlib import Path
from typing import TypeVar

from code_agent.core.events import EventKind
from code_agent.core.task import TaskStatus


_T = TypeVar("_T")


def plugin_event_fields(event: object) -> dict[str, object]:
    allowed = {"status", "name", "turn", "task_id", "thread_id", "request_id"}
    return {
        key: value
        for key, value in event.payload.items()
        if key in allowed
        and (isinstance(value, (str, int, bool, float)) or value is None)
    }


def plugin_event_kind(event: object) -> str:
    if event.kind is EventKind.TASK_STATUS_CHANGED:
        status = event.payload.get("status")
        if isinstance(status, str):
            return f"task_{status}"
    if event.kind is EventKind.COMPLETED:
        return "run_completed"
    return event.kind.value


def active_task(task: object) -> bool:
    return task.status in {TaskStatus.CREATED, TaskStatus.RUNNING}


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(
        str(right.resolve())
    )


async def settle_despite_cancellation(
    coroutine: Coroutine[object, object, _T],
) -> _T:
    worker = asyncio.create_task(coroutine)
    while True:
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            if worker.done():
                return worker.result()
