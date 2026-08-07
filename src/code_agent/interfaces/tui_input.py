from __future__ import annotations

from typing import Any

from .attachment_input import dropped_file_paths
from .input_events import paste_event
from .terminal_display import DisplayKind


async def handle_interrupt(app: Any) -> None:
    if await _handle_modal_interrupt(app):
        return
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


async def _handle_modal_interrupt(app: Any) -> bool:
    interactions = getattr(app, "interactions", None)
    if interactions is None:
        return False
    higher_modal = any(
        getattr(app, name, None) is not None
        for name in ("_pending_approval", "_pending_interaction", "_rewind_flow")
    )
    if higher_modal and await interactions.handle_key(app, "\x1b"):
        app.exit_guard.input_received()
        return True
    diff_modal = getattr(getattr(app, "interactions", None), "diff_interaction", None)
    if diff_modal is not None and diff_modal.active:
        diff_modal.request_close()
        app.exit_guard.input_received()
        return True
    return False


async def apply_paste(app: Any, value: str) -> bool:
    draft = getattr(app, "attachment_draft", None)
    if value == "" and draft is not None:
        return await apply_clipboard_images(app)
    paths = dropped_file_paths(value)
    if paths and draft is not None:
        try:
            added = await draft.add_paths(paths)
        except (RuntimeError, ValueError) as error:
            app._append(DisplayKind.ERROR, str(error))
            return False
        app._append(
            DisplayKind.METADATA,
            f"file drop staged · {len(added)} · total {len(draft.items)}",
        )
        app.exit_guard.input_received()
        return True
    try:
        event = paste_event(value)
    except (TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app.input.insert(event.value)
    app.exit_guard.input_received()
    return True


async def apply_clipboard_images(app: Any) -> bool:
    """Stage clipboard images when the composer receives Ctrl+V."""
    draft = getattr(app, "attachment_draft", None)
    if draft is None:
        app._append(DisplayKind.ERROR, "attachment input is unavailable")
        return False
    try:
        added = await draft.add_clipboard_items()
    except (RuntimeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(
        DisplayKind.METADATA,
        f"clipboard images staged · {len(added)} · total {len(draft.items)}",
    )
    app.exit_guard.input_received()
    return True
