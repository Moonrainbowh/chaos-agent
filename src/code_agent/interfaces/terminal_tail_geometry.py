from __future__ import annotations

from dataclasses import dataclass
from .terminal_display import clip_display, display_width, graphemes, grapheme_width, safe_text


@dataclass(frozen=True)
class LiveTailGeometry:
    height: int
    cursor_row: int


@dataclass(frozen=True)
class LiveTailFrame:
    text: str
    geometry: LiveTailGeometry


def _compact_frame(
    input_text: str, status: str, width: int, height: int,
    cursor_index: int | None, previous: LiveTailGeometry | None,
) -> LiveTailFrame:
    supplied = safe_text(input_text)
    index = len(supplied) if cursor_index is None else min(max(0, cursor_index), len(supplied))
    rows, cursor_row, cursor_column = _layout_input(supplied, max(1, width - 2), index)
    active = rows[cursor_row] if supplied else ""
    composer = clip_display(("› " if width > 1 else "") + active, width)
    lines = [composer]
    if height > 1:
        lines.append(clip_display(safe_text(status).replace("\n", " "), width))
    geometry = LiveTailGeometry(len(lines), 0)
    column = min(max(0, width - 1), cursor_column + (2 if width > 1 else 0))
    return LiveTailFrame(_rewrite_tail(lines, 0, column, previous, height), geometry)


def _visible_input_rows(
    rows: list[str], cursor_row: int, budget: int
) -> tuple[list[str], int]:
    start = min(max(0, cursor_row - budget + 1), max(0, len(rows) - budget))
    return rows[start:start + budget], cursor_row - start


def clear_live_tail(
    geometry: LiveTailGeometry | None, *, terminal_height: int | None = None
) -> str:
    """Erase a rendered dynamic tail and return the cursor to its top row."""
    if geometry is None:
        return "\r\x1b[2K"
    height = geometry.height
    if terminal_height is not None:
        height = min(height, max(1, terminal_height))
    cursor_row = min(geometry.cursor_row, height - 1)
    parts = ["\r"]
    if cursor_row:
        parts.append(f"\x1b[{cursor_row}A")
    for row in range(height):
        parts.append("\x1b[2K")
        if row < height - 1:
            parts.append("\n\r")
    parts.append("\r")
    if height > 1:
        parts.append(f"\x1b[{height - 1}A")
    return "".join(parts)


def _layout_input(value: str, width: int, cursor_index: int) -> tuple[list[str], int, int]:
    rows = [""]
    row_widths = [0]
    cursor_row = 0
    cursor_column = 0
    offset = 0
    for cluster in graphemes(value):
        source_length = len(cluster)
        if offset <= cursor_index < offset + len(cluster):
            cursor_row, cursor_column = len(rows) - 1, row_widths[-1]
        if cluster == "\n":
            rows.append("")
            row_widths.append(0)
            offset += len(cluster)
            continue
        cluster_width = grapheme_width(cluster)
        if cluster == "\t":
            # Never emit a hardware tab: its terminal column differs from the
            # editor's measured width and can wrap outside the tracked frame.
            cluster_width = min(4 - row_widths[-1] % 4, width)
            cluster = " " * cluster_width
        if rows[-1] and row_widths[-1] + cluster_width > width:
            rows.append("")
            row_widths.append(0)
        visible = cluster if cluster_width <= width else "?"
        rows[-1] += visible
        row_widths[-1] += display_width(visible)
        offset += source_length
    if cursor_index == len(value):
        cursor_row, cursor_column = len(rows) - 1, row_widths[-1]
    return rows, cursor_row, cursor_column


def _wrap_plain(value: str, width: int) -> list[str]:
    rows: list[str] = []
    for logical in value.split("\n"):
        clusters = list(graphemes(logical))
        if not clusters:
            rows.append("")
        while clusters:
            part, used = _take_row(clusters, width)
            rows.append(part)
            del clusters[:used]
    return rows


def _take_row(clusters: list[str], width: int) -> tuple[str, int]:
    result: list[str] = []
    used_width = 0
    for index, cluster in enumerate(clusters):
        cluster_width = grapheme_width(cluster)
        if cluster_width > width and not result:
            return "?", 1
        if used_width + cluster_width > width:
            return "".join(result), index
        result.append(cluster)
        used_width += cluster_width
    return "".join(result), len(clusters)


def _rewrite_tail(
    lines: list[str], cursor_row: int, cursor_column: int,
    previous: LiveTailGeometry | None, terminal_height: int,
) -> str:
    parts = ["\r"]
    previous_cursor = min(
        previous.cursor_row if previous else 0, terminal_height - 1
    )
    if previous_cursor:
        parts.append(f"\x1b[{previous_cursor}A")
    previous_height = min(previous.height if previous else 0, terminal_height)
    total_rows = min(terminal_height, max(len(lines), previous_height))
    for row in range(total_rows):
        parts.append("\x1b[2K")
        if row < len(lines):
            parts.append(lines[row])
        if row < total_rows - 1:
            parts.append("\n\r")
    parts.append("\r")
    rows_up = total_rows - 1 - cursor_row
    if rows_up:
        parts.append(f"\x1b[{rows_up}A")
    if cursor_column:
        parts.append(f"\x1b[{cursor_column}C")
    return "".join(parts)


def get_console_dock_padding(tail_height: int) -> int:
    """Return how many blank lines are needed to dock the tail at the viewport bottom.
    In append-only CLI flow, preserve compact inline layout without blank line craters.
    """
    return 0
