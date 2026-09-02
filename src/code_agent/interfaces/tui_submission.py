from __future__ import annotations

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
        return "排队" if self is SubmitMode.QUEUE else "转向"


def toggle_submit_mode(app: object) -> SubmitMode:
    current = getattr(app, "submit_mode", SubmitMode.QUEUE)
    app.submit_mode = (
        SubmitMode.STEER if current is SubmitMode.QUEUE else SubmitMode.QUEUE
    )
    app._append(DisplayKind.METADATA, f"运行中提交模式 · [{app.submit_mode.label}]")
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
            f"{app.submit_mode.label}提交失败 ({type(error).__name__})",
        )
        app.redraw()
        return False
    app._acknowledge_submission(EventKind.MESSAGE_ADDED, prepared)
    app.redraw()
    return True


async def pause_active_task(app: object, reason: str) -> bool:
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
