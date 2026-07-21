from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Sequence

from .interaction import InteractionResult
from .terminal_tail import clear_live_tail


_FRAME_INTERVAL = 1 / 30
_SPINNER_INTERVAL = 0.1
_CLOSE_GRACE_SECONDS = 0.1


def start_animation(app: object) -> None:
    if app._animation_task is None or app._animation_task.done():
        app._animation_task = asyncio.create_task(animate(app))


async def stop_animation(app: object) -> None:
    if app._animation_task and not app._animation_task.done():
        app._animation_task.cancel()
    if app._animation_task:
        await asyncio.gather(app._animation_task, return_exceptions=True)
    app._animation_task = None


async def animate(app: object) -> None:
    while app._run_task and not app._run_task.done():
        now = time.monotonic()
        size = shutil.get_terminal_size((100, 30))
        geometry_changed = app._drawn_size != (size.columns, size.lines)
        draft_changed = app._drawn_draft_revision != app.state.draft_revision
        spinner_due = app.state.status == "running" and now >= app._next_spinner_at
        if spinner_due:
            app._spinner_index += 1
            app._next_spinner_at = now + _SPINNER_INTERVAL
        if app._redraw_dirty or geometry_changed or draft_changed or spinner_due:
            app.redraw()
        await asyncio.sleep(_FRAME_INTERVAL)


async def listen_approvals(app: object) -> None:
    while True:
        app._pending_approval = await app.approvals.next_request()
        app._approval_done.clear()
        app.redraw()
        await app._approval_done.wait()


async def listen_interactions(app: object) -> None:
    while app.interaction_broker is not None:
        app._pending_interaction = await app.interaction_broker.next_request()
        app._interaction_done.clear()
        app.redraw()
        await app._interaction_done.wait()


async def close_tasks(app: object) -> None:
    if app._pending_approval is not None:
        app.approvals.resolve(app._pending_approval.request_id, False)
        app._pending_approval = None
        app._approval_done.set()
    if app._pending_interaction is not None and app.interaction_broker:
        app.interaction_broker.resolve(
            InteractionResult(
                app._pending_interaction.identifier,
                False,
                cancelled=True,
            )
        )
        app._pending_interaction = None
        app._interaction_done.set()
    await _request_cooperative_stop(app)
    if app._token:
        app._token.cancel("TUI closed")
    await _allow_run_to_finish(app)
    if app.state.has_draft:
        app.state._freeze_partial_answer()
        app._flush_pending_entries()
    app._write(clear_live_tail(app._tail_geometry))
    app._tail_geometry = None
    tasks = (
        app._run_task,
        app._approval_task,
        app._interaction_task,
        app._animation_task,
    )
    for task in tasks:
        if task:
            task.cancel()
    await asyncio.gather(
        *(task for task in tasks if task), return_exceptions=True
    )


async def _request_cooperative_stop(app: object) -> None:
    if not app.tasks or not app.active_task_id:
        return
    try:
        await asyncio.wait_for(
            app.tasks.interrupt(app.active_task_id, "TUI closed"),
            timeout=_CLOSE_GRACE_SECONDS,
        )
    except (asyncio.TimeoutError, Exception):
        return


async def _allow_run_to_finish(app: object) -> None:
    if not app._run_task or app._run_task.done():
        return
    try:
        await asyncio.wait_for(
            asyncio.shield(app._run_task), timeout=_CLOSE_GRACE_SECONDS
        )
    except (asyncio.TimeoutError, Exception):
        return


def format_command_help(specs: Sequence[object]) -> str:
    groups: dict[str, list[str]] = {}
    for spec in specs:
        groups.setdefault(spec.group, []).append(
            f"  {spec.display} · {spec.description}"
        )
    return "\n".join(
        line
        for group, commands in groups.items()
        for line in (group, *commands)
    )
