from __future__ import annotations

from typing import Any

from .attachment_input import dropped_file_paths
from .input_events import paste_event
from .terminal_display import DisplayKind
from .tui_submission import pause_active_task


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
    elif await pause_active_task(app, "user requested pause"):
        pass
    elif app._token:
        app._token.cancel("user requested pause")
    elif app.input.text or getattr(getattr(app, "attachment_draft", None), "items", ()):
        clear_input(app)
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
        app.input.insert_paste(event.value)
    except (TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
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


def insert_input(app: Any, value: str) -> None:
    """Reject oversized keyboard edits without changing or submitting the draft."""
    try:
        app.input.insert(value)
    except ValueError as error:
        app._append(DisplayKind.ERROR, str(error))


def sync_attachment_input(app: Any) -> None:
    draft = getattr(app, "attachment_draft", None)
    app.input.sync_images(getattr(draft, "image_tokens", ()))


def delete_input(app: Any, *, backwards: bool) -> bool:
    before = app.input.display
    removed = app.input.backspace() if backwards else app.input.delete()
    draft = getattr(app, "attachment_draft", None)
    if draft is not None:
        for identifier in removed:
            draft.remove(identifier)
    return app.input.display != before


def clear_input(app: Any) -> None:
    app.input.clear()
    draft = getattr(app, "attachment_draft", None)
    if draft is not None:
        draft.clear()
