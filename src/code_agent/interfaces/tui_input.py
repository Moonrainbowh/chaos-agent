from __future__ import annotations

from typing import Any

from .input_events import paste_event
from .terminal_display import DisplayKind


async def handle_interrupt(app: Any) -> None:
    if app.exit_guard.interrupt():
        app.running = False
        if app._token:
            app._token.cancel("TUI closed")
        return
    if app._pending_approval is not None:
        app.approvals.resolve(app._pending_approval.request_id, False)
        app._pending_approval = None
        app._approval_done.set()
    elif app.tasks and app.active_task_id:
        await app.tasks.pause(app.active_task_id, "user requested pause")
        app.state.status = "paused"
        app._append(DisplayKind.METADATA, "task paused")
    elif app._token:
        app._token.cancel("user requested pause")
    elif app.input.text:
        app.input.clear()
    else:
        app._append(DisplayKind.METADATA, "press Ctrl+C again within two seconds to exit")


def apply_paste(app: Any, value: str) -> bool:
    try:
        event = paste_event(value)
    except (TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app.input.insert(event.value)
    app.exit_guard.input_received()
    return True
