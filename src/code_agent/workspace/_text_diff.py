from __future__ import annotations

import difflib


def unified_text_diff(path: str, before: str, after: str, existed: bool) -> str:
    """Render the exact normalized text that an EditPlan will write."""
    return "".join(
        difflib.unified_diff(
            _diff_lines(before),
            _diff_lines(after),
            fromfile=f"a/{path}" if existed else "/dev/null",
            tofile=f"b/{path}",
        )
    )


def _diff_lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if lines and not text.endswith(("\n", "\r")):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines
