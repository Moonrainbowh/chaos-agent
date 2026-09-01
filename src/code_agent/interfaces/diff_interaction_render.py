from __future__ import annotations

from ._diff_parser import DiffLine
from .diff_view import DiffView
from .terminal_display import clip_display, display_width, graphemes, safe_text


DIFF_PAGE_SIZE = 6


def render_diff_interaction(
    view: DiffView | None,
    *,
    mode: str,
    line_index: int,
    editor_text: str,
    editor_cursor: int,
    error: str | None,
    columns: int,
    max_rows: int = 14,
    read_only: bool = False,
) -> tuple[str, ...]:
    """Render a bounded overlay for the point-in-time diff modal."""
    width = max(20, columns - 4)
    row_limit = max(0, min(14, max_rows))
    mode_rows = 2 if mode in {"discard", "filter", "comment"} else 0
    fixed_rows = 2 + int(bool(error)) + mode_rows
    line_budget = max(0, min(DIFF_PAGE_SIZE, row_limit - fixed_rows))
    rows = _browse_rows(view, line_index, width, line_budget, read_only=read_only)
    if error:
        rows.insert(1, clip_display("! " + safe_text(error), width))
    if mode == "discard":
        rows.extend(
            (
                clip_display("Discard unsent diff feedback?", width),
                clip_display("y discard · Enter/Esc/Ctrl+C keep", width),
            )
        )
    elif mode in {"filter", "comment"}:
        prefix = f"{mode} › "
        available = max(1, width - display_width(prefix))
        visible = _editor_text(editor_text, editor_cursor, available)
        rows.append(clip_display(prefix + visible, width))
        help_text = (
            "Enter save · Esc cancel · Ctrl+J newline"
            if mode == "comment"
            else "Enter apply · Esc cancel"
        )
        rows.append(clip_display(help_text, width))
    return tuple(rows[:row_limit])


def _browse_rows(
    view: DiffView | None,
    line_index: int,
    width: int,
    line_budget: int,
    *,
    read_only: bool,
) -> list[str]:
    current = view.current if view else None
    if current is None:
        controls = "Esc back" if read_only else "r refresh · Esc close"
        return [
            clip_display(value, width)
            for value in (
                "diff · point-in-time",
                "no diff available",
                controls,
            )
        ]
    provenance = "fresh" if current.fresh else "recorded"
    stale = " · stale" if view.stale else ""
    header = (
        f"{current.path} · {current.scope.value} · {provenance}{stale} · "
        f"+{current.additions} -{current.removals} · "
        f"file {view.selected_file + 1}/{len(view.files)}"
    )
    rows = [clip_display(_single_line(header), width)]
    start = _window_start(len(current.lines), line_index, line_budget)
    for index in range(start, min(start + line_budget, len(current.lines))):
        rows.append(_line_row(view, current.lines[index], index, line_index, width))
    controls = "↑↓ line · ←→ file · [] hunk · PgUp/PgDn"
    if read_only:
        controls += " · Esc back"
    else:
        controls += (
            f" · / filter · c comment · s send ({len(view.comments)})"
            " · r · Esc"
        )
    rows.append(clip_display(controls, width))
    return rows


def _line_row(
    view: DiffView, line: DiffLine, index: int, selected: int, width: int
) -> str:
    current = view.current
    marked = bool(
        current
        and any(
            item.scope is current.scope
            and item.path == current.path
            and item.line_index == index
            for item in view.comments
        )
    )
    prefix = "›" if index == selected else " "
    return clip_display(
        f"{prefix}{'●' if marked else ' '} {_line_anchor(line, index):>9} "
        f"{safe_text(line.text)}",
        width,
    )


def _line_anchor(line: DiffLine, index: int) -> str:
    if line.old_line is not None and line.new_line is not None:
        return f"{line.old_line}:{line.new_line}"
    if line.new_line is not None:
        return f"+{line.new_line}"
    if line.old_line is not None:
        return f"-{line.old_line}"
    return f"@{index + 1}"


def _editor_text(value: str, cursor: int, width: int) -> str:
    cursor = min(max(0, cursor), len(value))
    before = _editor_segment(value[:cursor])
    after = _editor_segment(value[cursor:])
    right_target = min(display_width(after), max(0, (width - 1) // 2))
    left = _clip_right(before, max(0, width - 1 - right_target))
    right = clip_display(after, max(0, width - 1 - display_width(left)))
    return left + "│" + right


def _clip_right(value: str, width: int) -> str:
    result: list[str] = []
    used = 0
    for cluster in reversed(graphemes(value)):
        cluster_width = display_width(cluster)
        if used + cluster_width > width:
            break
        result.append(cluster)
        used += cluster_width
    return "".join(reversed(result))


def _window_start(total: int, selected: int, budget: int) -> int:
    if budget <= 0:
        return 0
    selected = min(max(0, selected), max(0, total - 1))
    start = (selected // DIFF_PAGE_SIZE) * DIFF_PAGE_SIZE
    if selected >= start + budget:
        start = selected - budget + 1
    return min(start, max(0, total - budget))


def _editor_segment(value: str) -> str:
    return safe_text(value).replace("\n", "↵").replace("\t", "⇥")


def _single_line(value: str) -> str:
    return safe_text(value).replace("\n", " ").replace("\t", " ")
