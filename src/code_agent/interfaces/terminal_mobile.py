"""Compact terminal composer, footer and touch controls."""
from __future__ import annotations

from .terminal_display import clip_display, display_width, safe_text
from .terminal_layout import TouchRegion
from .terminal_style import ColorMode, DIM_GRAY, colorize
from .terminal_tail_content import _render_draft
from .terminal_tail_geometry import (
    LiveTailFrame, LiveTailGeometry, _layout_input, _rewrite_tail,
    _visible_input_rows, tail_geometry,
)


def render_mobile_frame(
    text: str, status: str, width: int, height: int,
    cursor_index: int | None, draft: str, color: ColorMode,
    palette: tuple[str, ...], context: str, previous: LiveTailGeometry | None,
    *, expanded: bool, active: bool, palette_actions: tuple[str, ...] = (), modal: bool = False,
    submit_label: str = "排队",
) -> LiveTailFrame:
    """Fit controls and the input cursor into the available terminal viewport."""
    supplied = safe_text(text)
    index = len(supplied) if cursor_index is None else min(max(0, cursor_index), len(supplied))
    # Keep at least one row available for the transcript in ordinary viewports.
    budget = max(1, height - 1)
    controls = 2 if width >= 24 and budget >= 7 else 0
    if modal and controls:
        controls = 1
    extra = int(bool(controls and expanded and not modal))
    status_rows = 2 if budget >= 4 else 1
    input_budget = max(1, min(3, budget - controls - extra - status_rows - 1))
    rows, cursor_row, cursor_column = _layout_input(supplied, max(1, width - 2), index)
    if index == len(supplied) and cursor_column == width - 2:
        rows.append("")
        cursor_row, cursor_column = len(rows) - 1, 0
    rows, cursor_row = _visible_input_rows(rows, cursor_row, input_budget if expanded else 1)
    overhead = len(rows) + status_rows + controls + extra
    remaining = max(0, budget - overhead)
    items = palette[:remaining]
    remaining -= len(items)
    lines = [clip_display(safe_text(item), width) for item in items]
    regions = [TouchRegion(action, row, 0, width)
               for row, action in enumerate(palette_actions[:len(items)]) if action]
    lines += _render_draft(draft, width, remaining, color, modern=True)
    offset = len(lines)
    if expanded:
        lines += [clip_display("› " + row, width) for row in rows]
    else:
        lines += [clip_display("› " + (supplied.replace("\n", " ") or "点击输入"), width)]
        cursor_row, cursor_column = 0, 0
    summary = safe_text(status).replace("\n", " ")
    lines.append(colorize(clip_display(summary, width), DIM_GRAY, color))
    if status_rows == 2:
        parts = safe_text(context.split("\n")[-1]).split(" · ")
        important = [part for part in parts if "token/s" in part or "tokens" in part]
        rest = [part for part in parts if part not in important]
        metadata = " · ".join(important)
        available = width - display_width(metadata) - (3 if metadata else 0)
        model = clip_display(" · ".join(rest), max(0, available))
        lines.append(colorize(clip_display(metadata + (" · " if metadata and model else "") + model, width), DIM_GRAY, color))
    if controls:
        actions = (("compose", "收起" if expanded else "输入"), ("commands", "命令"),
                   ("latest", "到底"), ("stop" if active else "status", "暂停" if active else "状态"))
        if modal:
            actions = (("back", "取消"), ("submit", "确认"), ("up", "上移"), ("down", "下移"))
        if controls == 2:
            icons = {"compose": "✎", "commands": ":", "latest": "↓", "stop": "■", "status": "≡"}
            icon_line, icon_targets = control_row(tuple((action, icons[action]) for action, _ in actions), width, len(lines), bracket=False)
            lines.append(icon_line); regions.extend(icon_targets)
        line, targets = control_row(actions, width, len(lines), bracket=modal)
        lines.append(line); regions.extend(targets)
        if extra:
            actions = (("newline", "换行"), ("submit", submit_label if active else "发送"),
                       ("up", "上移"), ("down", "下移")) if expanded else (
                           ("up", "上移"), ("down", "下移"), ("back", "返回"), ("compose", "输入"))
            line, targets = control_row(actions, width, len(lines))
            lines.append(line); regions.extend(targets)
    column = min(width - 1, cursor_column + min(2, width - 1))
    geometry = tail_geometry(lines, offset + cursor_row, column)
    return LiveTailFrame(_rewrite_tail(lines, geometry.cursor_row, column, previous, height), geometry, tuple(regions))


def control_row(actions: tuple[tuple[str, str], ...], width: int, row: int, *, bracket: bool = True) -> tuple[str, tuple[TouchRegion, ...]]:
    """Build labels and hit regions using identical display-column boundaries."""
    parts, regions = [], []
    for index, (action, label) in enumerate(actions):
        left, right = width * index // len(actions), width * (index + 1) // len(actions)
        value = clip_display("[" + label + "]" if bracket else label, right - left)
        padding = max(0, right - left - display_width(value))
        parts.append(" " * (padding // 2) + value + " " * (padding - padding // 2))
        regions.append(TouchRegion(action, row, left, right))
    return "".join(parts), tuple(regions)
