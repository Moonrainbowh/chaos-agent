"""Pure terminal-cell rendering for the full-screen CHAOS startup scan."""
from __future__ import annotations


_LETTERS = (
    ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
)
_LOGO = tuple("00".join(letter[row] for letter in _LETTERS) for row in range(7))
_BLUE = "\x1b[38;2;125;211;252m"
_EDGE = "\x1b[38;2;195;235;255m"
_RESET = "\x1b[0m"


def scan_frame(width: int, height: int, progress: float, *, color: bool = True) -> str:
    """Render a bounded centered logo; reveal columns monotonically left to right.

    Keep the last terminal column unused to avoid auto-wrap or scrolling.
    Tiny terminals use the literal title instead of a cropped block alphabet.
    """
    width, height = max(1, width), max(1, height)
    usable = max(0, width - 1)
    progress = max(0.0, min(1.0, progress))
    if usable < 35 or height < 9:
        title = "CHAOS"[:usable]
        shown = int(len(title) * progress)
        row = title[:shown] + " " * (len(title) - shown)
        prefix = _BLUE if color else ""
        return _at(max(0, (height - 1) // 2), max(0, (usable - len(title)) // 2)) + prefix + row + (_RESET if color else "")
    sx = max(1, min(6, (usable - 4) // 33))
    sy = max(1, min(sx // 2, (height - 4) // 7))
    rows = tuple("".join("█" * sx if bit == "1" else " " * sx for bit in row) for row in _LOGO)
    columns = 33 * sx
    edge = int(columns * progress)
    left, top = (usable - columns) // 2, (height - 7 * sy) // 2
    output = []
    for y, row in enumerate(rows):
        visible = row[:edge]
        if color:
            # The advancing edge is brighter; completed columns stay ice blue.
            bright = max(0, edge - sx) if progress < 1 else edge
            visible = _BLUE + visible[:bright] + _EDGE + visible[bright:] + _RESET
        for repeat in range(sy):
            output.append(_at(top + y * sy + repeat, left) + visible + " " * (columns - edge))
    return "".join(output)


def _at(row: int, column: int) -> str:
    return f"\x1b[{row + 1};{column + 1}H"
