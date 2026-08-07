from __future__ import annotations

import json
from collections.abc import Sequence

from .diff_view import DiffComment


MAX_DIFF_FEEDBACK_BYTES = 16 * 1024
MAX_DIFF_FEEDBACK_CHARS = 1_024


def format_diff_feedback(comments: Sequence[DiffComment]) -> str:
    """Serialize bounded, scope-qualified review comments for normal submission."""
    if not comments:
        raise ValueError("add at least one diff comment before sending")
    body = "\n".join(_comment_json(comment) for comment in comments)
    result = (
        "Diff review feedback (point-in-time snapshot):\n"
        + body
        + "\nPlease address every anchored comment."
    )
    if (
        len(result) > MAX_DIFF_FEEDBACK_CHARS
        or len(result.encode("utf-8")) > MAX_DIFF_FEEDBACK_BYTES
    ):
        raise ValueError("diff feedback is too large to send")
    return result


def _comment_json(comment: DiffComment) -> str:
    anchor = {
        "old": comment.old_line,
        "new": comment.new_line,
        "hunk": comment.hunk,
        "diff_index": comment.line_index,
    }
    value = {
        "scope": comment.scope.value,
        "path": comment.path,
        "anchor": anchor,
        "comment": comment.text,
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
