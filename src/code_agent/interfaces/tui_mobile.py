"""Route compact terminal touches through existing keyboard/controller paths."""
from __future__ import annotations

from .input_buffer import InputBuffer
from .terminal_display import clip_display
from .terminal_layout import LayoutMode
from .terminal_mouse import MouseClick
from .terminal_size import terminal_size
from .tui_auth_prompt import auth_active
from .tui_submission import pause_active_task


def modal_active(app) -> bool:
    return bool(auth_active(app) or app._pending_approval is not None
                or getattr(app, "_project_picker", None) is not None
                or getattr(app, "_pending_interaction", None) is not None
                or getattr(app, "_rewind_flow", None) is not None
                or app.interactions.diff_interaction.active
                or getattr(app.interactions.session_tree, "active", False))


def mobile_palette(app, rows: tuple[str, ...], columns: int, height: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Expose only the actual command picker items as single-click rows."""
    if modal_active(app) or not app.input.text.startswith(("/", ":")):
        return rows, ()
    picker = app.interactions.picker
    matches = picker.matches
    count = min(len(picker.visible), max(1, height - 8))
    start = min(max(0, picker.selected_index - count + 1), max(0, len(matches) - count))
    items = matches[start:start + count]
    title = clip_display(picker.title, columns)
    labels = (title,) + tuple(clip_display(
        ("› " if item == picker.selected else "  ") + item.label + " · "
        + (item.detail if item.enabled else item.disabled_reason or "disabled"), columns,
    ) for item in items)
    return labels, ("",) + tuple("picker:" + item.identifier for item in items)


def restore_mobile_draft(app) -> None:
    saved = getattr(app, "_mobile_saved_input", None)
    if saved is not None and not app.input.text.startswith(("/", ":")):
        original, thread_id = saved
        app._mobile_saved_input = None
        if app.current_thread_id == thread_id:
            app.input = original


async def handle_touch(app, event: MouseClick) -> None:
    size = terminal_size((100, 30))
    frame = getattr(app, "_mobile_frame", None)
    if (frame is None or app._tail_geometry is None or app._drawn_size != tuple(size)
            or app.layout_mode.resolve(size.columns) is not LayoutMode.COMPACT):
        return
    column = event.column - 1
    header = next((area for area in getattr(app, "_mobile_header_regions", ())
                   if area.contains(event.row - 1, column)), None)
    if header is not None:
        if modal_active(app):
            return
        if header.action == "sessions":
            if app.input.text and not app.input.text.startswith(("/", ":")):
                app._mobile_saved_input = (app.input, app.current_thread_id)
                app.input = InputBuffer()
            await app.submit("/sessions history")
            app.composer_expanded = True
        elif header.action == "projects":
            from .tui_projects import open_project_picker
            await open_project_picker(app)
        app.redraw()
        return
    row = event.row - 1 - (size.lines - frame.geometry.height)
    target = next((area for area in frame.touch_regions if area.contains(row, column)), None)
    if target is None:
        return
    action = target.action
    if modal_active(app):
        key = {"submit": "\r", "back": "\x1b", "up": "up", "down": "down"}.get(action)
        if key is not None:
            await app.handle_key(key)
        return
    if action.startswith("picker:"):
        app.interactions.rows(app)
        picker = app.interactions.picker
        identifier = action.removeprefix("picker:")
        item = next((item for item in picker.visible if item.identifier == identifier), None)
        if item is not None:
            picker.selected_index = picker.matches.index(item)
            await app.handle_key("\r")
        return
    if action == "compose":
        app.composer_expanded = not app.composer_expanded
    elif action == "commands":
        if app.input.text.startswith(("/", ":")):
            app.input.replace("")
            restore_mobile_draft(app)
        else:
            app._mobile_saved_input = (app.input, app.current_thread_id)
            app.input = InputBuffer()
            app.input.insert("/")
        app.composer_expanded = True
    elif action == "latest":
        app.scroll_to_bottom()
    elif action == "stop":
        await pause_active_task(app, "mobile user requested pause")
    elif action == "status":
        await app.submit("/status")
    elif action == "back":
        await app.handle_key("\x1b")
    elif action in {"up", "down"}:
        if app.input.text.startswith(("/", ":")):
            await app.handle_key(action)
        else:
            app.scroll_viewport(2 if action == "up" else -2)
    elif action == "newline":
        await app.handle_key("\n")
    elif action == "submit":
        await app.handle_key("\r")
    app.redraw()
