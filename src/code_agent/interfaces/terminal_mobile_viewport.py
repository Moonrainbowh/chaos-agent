"""Bounded compact viewport projected from the same trusted transcript renderer."""
from __future__ import annotations

from .terminal_display import DisplayKind, clip_display, display_width, safe_text
from .terminal_layout import TouchRegion
from .terminal_renderer import render_entries
from .terminal_style import color_enabled
from .terminal_markdown import render_streaming_markdown_rows


def mobile_history(entries, width, theme, color, previous=None):
    """Reuse Markdown and entry spacing, changing only the compact user surface."""
    lines = []
    for entry in entries:
        rendered = render_entries((entry,), width, theme=theme, color=color, previous=previous)
        if entry.kind is DisplayKind.USER and color_enabled(color):
            rendered = rendered.replace("48;2;30;48;76", "48;2;23;48;45")
            # Other themes have no user surface; keep role shading consistent.
            if "48;2;23;48;45" not in rendered:
                bg = "\x1b[48;2;23;48;45m"
                rendered = "\n".join(bg + row.replace("\x1b[0m", "\x1b[0m" + bg)
                    + "\x1b[0m" for row in rendered.split("\n"))
        lines.extend(rendered.splitlines())
        previous = entry
    return lines


def cached_mobile_history(app, width):
    """Reformat only appended entries; state animation does not reparse history."""
    entries = app.state.entries
    key = (id(entries), width, app.theme, app.color, color_enabled(app.color))
    count = getattr(app, "_compact_history_count", 0)
    reusable = (getattr(app, "_compact_history_key", None) == key and len(entries) >= count
                and (not count or entries[count - 1] is app._compact_history_last))
    if not reusable:
        app._compact_history_lines = mobile_history(entries, width, app.theme, app.color)
    elif len(entries) > count:
        app._compact_history_lines.extend(mobile_history(entries[count:], width, app.theme,
                                                        app.color, entries[count - 1] if count else None))
    app._compact_history_key, app._compact_history_count = key, len(entries)
    app._compact_history_last = entries[-1] if entries else None
    return app._compact_history_lines


def mobile_header(project, thread, status, width, height):
    """Two fixed rows with separate project and conversation touch targets."""
    rows, targets = [], []
    for row, (value, label, action) in enumerate((
        ("项目 " + safe_text(project), "[项目]", "projects"),
        ("会话 " + safe_text(thread or "新会话") + " · " + safe_text(status), "[会话]", "sessions"),
    )):
        if row >= height:
            break
        button = clip_display(label, min(6, width))
        left = max(0, width - display_width(button))
        text = clip_display(value.replace("\n", " "), max(0, left - 1))
        rows.append(text + " " * max(0, left - display_width(text)) + button)
        targets.append(TouchRegion(action, row, left, width))
    return rows, tuple(targets)


def render_mobile_viewport(app, frame, size, status):
    """Top-align short history; retain a stable header while paging long history."""
    header, targets = mobile_header(app.project_name, app.current_thread_id,
                                    status, size.columns, max(0, size.lines - frame.geometry.height))
    app._mobile_header_regions = targets
    body_height = max(0, size.lines - len(header) - frame.geometry.height)
    # The durable cache must never acquire ephemeral streaming rows.
    lines = list(cached_mobile_history(app, size.columns))
    if app.state.draft_answer:
        lines += ["", "◆ 正在回复"] + render_streaming_markdown_rows(
            app.state.draft_answer, size.columns, app.color)
    app._viewport_offset = min(app._viewport_offset, max(0, len(lines) - body_height))
    end = len(lines) - app._viewport_offset
    body = lines[max(0, end - body_height):end] if body_height else []
    rows = header + body + [""] * (body_height - len(body))
    prefix = "\x1b[2J\x1b[H" + "\n\r".join(rows)
    if rows:
        prefix += "\n\r"
    return prefix + frame.text
