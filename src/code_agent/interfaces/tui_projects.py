"""Project overlay delegates all directory changes to an injected store/Host."""
from __future__ import annotations

from .project_picker import ProjectPicker
from .terminal_display import DisplayKind
from .terminal_mouse import MouseClick
from .terminal_size import terminal_size


def _higher_modal(app):
    from .tui_auth_prompt import auth_active
    return bool(auth_active(app) or app._pending_approval is not None
                or getattr(app, "_pending_interaction", None) is not None
                or getattr(app, "_rewind_flow", None) is not None
                or app.interactions.diff_interaction.active
                or getattr(app.interactions.session_tree, "active", False))


async def open_project_picker(app):
    from .tui_mobile import modal_active
    if modal_active(app) or (app._run_task and not app._run_task.done()) or getattr(app, "_starting_task", False):
        app._append(DisplayKind.WARNING, "请先暂停当前任务并关闭弹窗，再切换项目")
        return False
    store = getattr(app, "project_store", None)
    if store is None:
        app._append(DisplayKind.WARNING, "请通过手机项目入口启动，以使用项目切换")
        return False
    draft = getattr(app.attachment_draft, "items", ())
    saved = getattr(app, "_mobile_saved_input", None)
    has_draft = bool(app.input.text or draft or (saved and saved[0].text))
    picker = ProjectPicker(store, current_root=app.workspace_root, has_draft=has_draft)
    await picker.load()
    app._project_picker = picker
    app.redraw()
    return True


async def handle_project_key(app, key):
    picker = getattr(app, "_project_picker", None)
    if picker is None:
        return False
    # A newly arrived auth/approval interaction retains priority over this overlay.
    if _higher_modal(app):
        return False
    if isinstance(key, MouseClick):
        size = terminal_size((100, 30))
        frame = getattr(app, "_project_frame", None)
        if frame is None or getattr(app, "_project_drawn_size", None) != tuple(size):
            return True
        target = next((area for area in frame.touch_regions if area.contains(key.row - 1, key.column - 1)), None)
        if target:
            await picker.action(target.action)
    else:
        try:
            await picker.handle_key(key)
        except ValueError as error:
            picker.error = str(error)
    if picker.finished:
        app._project_picker = None
        if picker.result is not None and picker.result != app.workspace_root:
            app.requested_project = picker.result
            app.running = False
        app._viewport_needs_full_redraw = True
    app.redraw()
    return True


def render_project_overlay(app, size):
    picker = getattr(app, "_project_picker", None)
    if picker is None:
        return False
    if _higher_modal(app):
        return False
    frame = picker.frame(*size)
    app._project_frame, app._project_drawn_size = frame, tuple(size)
    app._mobile_frame = None
    app._mobile_header_regions = ()
    app._tail_geometry = None
    app._drawn_size = tuple(size)
    app._write("\x1b[?25l" + frame.text + "\x1b[?25h")
    return True
