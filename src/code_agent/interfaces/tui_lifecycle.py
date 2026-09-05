from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Sequence

from .interaction import InteractionResult
from .terminal_motion import exit_transition
from .terminal_tail import clear_live_tail
from .checkpoint_tui import close_rewind_flow


_FRAME_INTERVAL = 1 / 30
_SPINNER_INTERVAL = 0.1
_CLOSE_GRACE_SECONDS = 0.1
_ANIMATED_STATUSES = frozenset(
    {
        "running",
        "preparing_workspace",
        "pausing",
        "building_context",
        "waiting_model",
        "reasoning",
        "streaming_response",
        "preparing_action",
    }
)


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
        redraw, spinner_due = needs_animation_frame(
            dirty=app._redraw_dirty,
            drawn_size=app._drawn_size,
            current_size=(size.columns, size.lines),
            drawn_revision=app._drawn_draft_revision,
            current_revision=app.state.draft_revision,
            status=app.state.status,
            now=now,
            spinner_deadline=app._next_spinner_at,
        )
        if spinner_due:
            app._spinner_index += 1
            app._next_spinner_at = now + _SPINNER_INTERVAL
            update_title = getattr(app, "update_terminal_title", None)
            if callable(update_title):
                update_title(running=True)
        if redraw:
            app.redraw()
        await asyncio.sleep(_FRAME_INTERVAL)
    on_finish = getattr(app, "on_task_finished", None)
    if callable(on_finish):
        on_finish()


def needs_animation_frame(
    *, dirty: bool, drawn_size: tuple[int, int] | None,
    current_size: tuple[int, int], drawn_revision: int, current_revision: int,
    status: str, now: float, spinner_deadline: float,
) -> tuple[bool, bool]:
    """Decide one animation tick without sleeping or reading global state."""
    spinner_due = status in _ANIMATED_STATUSES and now >= spinner_deadline
    changed = drawn_size != current_size or drawn_revision != current_revision
    return dirty or changed or spinner_due, spinner_due


async def listen_approvals(app: object) -> None:
    while True:
        app._pending_approval = await app.approvals.next_request()
        app._approval_done.clear()
        on_approval = getattr(app, "on_approval_requested", None)
        if callable(on_approval):
            on_approval()
        app.redraw()
        await app._approval_done.wait()
        update_title = getattr(app, "update_terminal_title", None)
        if callable(update_title):
            update_title()


async def listen_interactions(app: object) -> None:
    while app.interaction_broker is not None:
        app._pending_interaction = await app.interaction_broker.next_request()
        app._interaction_done.clear()
        on_approval = getattr(app, "on_approval_requested", None)
        if callable(on_approval):
            on_approval()
        app.redraw()
        await app._interaction_done.wait()
        update_title = getattr(app, "update_terminal_title", None)
        if callable(update_title):
            update_title()


async def close_tasks(app: object) -> None:
    app._closing = True
    app.running = False
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
    if app._token:
        app._token.cancel("TUI closed")
    if getattr(app, "_starting_task", False) and app._run_task:
        app._run_task.cancel()
        await asyncio.gather(app._run_task, return_exceptions=True)
    await close_rewind_flow(app)
    await _await_durable_interrupt(app)
    await _allow_run_to_finish(app)
    if app.state.has_draft:
        app.state._freeze_partial_answer()
        app._flush_pending_entries()
    await stop_animation(app)
    visual_task = getattr(app, "_visual_task", None)
    if visual_task:
        visual_task.cancel()
        await asyncio.gather(visual_task, return_exceptions=True)
    if hasattr(app, "motion"):
        await exit_transition(app)
    height = shutil.get_terminal_size((100, 30)).lines
    app._write(clear_live_tail(app._tail_geometry, terminal_height=height))
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


async def _await_durable_interrupt(app: object) -> None:
    if not app.tasks or not app.active_task_id:
        return
    await app.tasks.interrupt(app.active_task_id, "TUI closed")


async def _allow_run_to_finish(app: object) -> None:
    if not app._run_task or app._run_task.done():
        return
    try:
        await asyncio.wait_for(
            asyncio.shield(app._run_task), timeout=_CLOSE_GRACE_SECONDS
        )
    except (asyncio.CancelledError, Exception):
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
