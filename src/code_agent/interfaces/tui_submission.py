from __future__ import annotations

import asyncio
from enum import Enum

from code_agent.core.events import EventKind

from .attachment_input import PreparedInput
from .terminal_display import DisplayKind
from .tui_active_input import queue_followup, steer_input


class SubmitMode(str, Enum):
    QUEUE = "queue"
    STEER = "steer"

    @property
    def label(self) -> str:
        return "queue" if self is SubmitMode.QUEUE else "steer"


def toggle_submit_mode(app: object) -> SubmitMode:
    current = getattr(app, "submit_mode", SubmitMode.QUEUE)
    app.submit_mode = (
        SubmitMode.STEER if current is SubmitMode.QUEUE else SubmitMode.QUEUE
    )
    app._append(DisplayKind.METADATA, f"Submit mode · [{app.submit_mode.label}]")
    return app.submit_mode


async def submit_active_input(app: object, prepared: PreparedInput) -> bool:
    try:
        if app.submit_mode is SubmitMode.QUEUE:
            await queue_followup(
                app.interactions,
                app, app.active_task_id, prepared.prompt, prepared.attachments
            )
        else:
            await steer_input(
                app.interactions,
                app, app.active_task_id, prepared.prompt, prepared.attachments
            )
    except Exception as error:
        app._append(
            DisplayKind.ERROR,
            f"{app.submit_mode.label} submission failed ({type(error).__name__})",
        )
        app.redraw()
        return False
    app._acknowledge_submission(EventKind.MESSAGE_ADDED, prepared)
    app.redraw()
    return True


async def pause_active_task(app: object, reason: str) -> bool:
    if getattr(app, "_starting_task", False) and app._run_task:
        app.state.status = "pausing"
        app._token.cancel(reason)
        app._request_redraw(immediate=True)
        app._run_task.cancel()
        await asyncio.gather(app._run_task, return_exceptions=True)
        app.state.status = "paused"
        app._request_redraw(immediate=True)
        return True
    if not (
        app.tasks
        and app.active_task_id
        and app._run_task
        and not app._run_task.done()
    ):
        return False
    app.state.status = "pausing"
    app._request_redraw(immediate=True)
    await app.tasks.pause(app.active_task_id, reason)
    app.state.status = "paused"
    app._append(DisplayKind.METADATA, "task paused")
    return True


def request_pause_active_task(app: object, reason: str) -> bool:
    """Start pausing without occupying the keyboard dispatch loop."""
    pending = getattr(app, "_pause_task", None)
    if pending is not None and not pending.done():
        return True
    can_pause = bool(
        (getattr(app, "_starting_task", False) and app._run_task)
        or (
            app.tasks
            and app.active_task_id
            and app._run_task
            and not app._run_task.done()
        )
    )
    if not can_pause:
        return False
    app.state.status = "pausing"
    app._request_redraw(immediate=True)
    app._pause_task = asyncio.create_task(_finish_requested_pause(app, reason))
    return True


async def _finish_requested_pause(app: object, reason: str) -> None:
    try:
        await pause_active_task(app, reason)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        app.state.status = "error"
        app._append(
            DisplayKind.ERROR,
            f"pause failed ({type(error).__name__})",
        )
    finally:
        current = asyncio.current_task()
        if getattr(app, "_pause_task", None) is current:
            app._pause_task = None
        app._request_redraw(immediate=True)
