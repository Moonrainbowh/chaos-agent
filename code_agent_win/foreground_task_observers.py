from __future__ import annotations

import asyncio

from code_agent.core.cancellation import CancellationToken
from code_agent.core.task import TaskStatus
from code_agent.workflows.observations import TaskCreatedObservation

from code_agent_win.foreground_task_support import settle_despite_cancellation


_INTERRUPT_ATTEMPTS = 3
_INTERRUPT_RETRY_DELAY_S = 0.01


async def observe_task_created(
    workflows: object,
    plugin_events: object | None,
    sessions: object,
    task: object,
    prompt: str,
) -> None:
    try:
        await workflows.observe(
            TaskCreatedObservation(task.id, task.thread_id, prompt)
        )
        if plugin_events is not None:
            await plugin_events.observe(
                "task_created",
                task.id,
                {"task_id": task.id, "thread_id": task.thread_id},
                CancellationToken(),
            )
    except BaseException as primary:
        try:
            await settle_despite_cancellation(
                _interrupt_created_task(sessions, task.id)
            )
        except BaseException as compensation:
            _add_note(
                primary,
                "task interruption compensation failed after "
                f"{_INTERRUPT_ATTEMPTS} attempts: {compensation}",
            )
        raise


async def _interrupt_created_task(sessions: object, task_id: str) -> None:
    last_error: Exception | None = None
    for attempt in range(_INTERRUPT_ATTEMPTS):
        try:
            task = await sessions.load_task(task_id)
            if task.status is TaskStatus.CREATED:
                await sessions.transition_task(
                    task_id,
                    TaskStatus.INTERRUPTED,
                    "task initialization observer failed",
                )
            return
        except Exception as error:
            last_error = error
            if attempt + 1 < _INTERRUPT_ATTEMPTS:
                await asyncio.sleep(_INTERRUPT_RETRY_DELAY_S)
    assert last_error is not None
    raise last_error


def _add_note(error: BaseException, note: str) -> None:
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
