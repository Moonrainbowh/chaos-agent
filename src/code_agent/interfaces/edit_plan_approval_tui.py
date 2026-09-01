from __future__ import annotations

from ._diff_parser import DiffScope
from .approval import ApprovalRequest
from .diff_interaction import DiffInteraction
from .diff_view import DiffView
from .terminal_display import clip_display, safe_text


_DIFF_KEYS = frozenset(
    {
        "\x1b", "q", "up", "k", "down", "j", "left", "p", "right", "n",
        "[", "]", "page_up", "page_down",
    }
)


def edit_plan_approval_rows(
    request: ApprovalRequest,
    choice: int,
    diff: DiffInteraction,
    *,
    columns: int,
    max_rows: int,
) -> tuple[str, ...] | None:
    preview = getattr(request, "edit_plan", None)
    if preview is None:
        return None
    if diff.active:
        return diff.rows(columns, max_rows=max_rows, read_only=True)
    risk = f" · risk {request.risk}" if request.risk else ""
    rows = [
        f"approval · {request.name}{risk}",
        clip_display(f"plan: {safe_text(preview.plan_id)}", columns),
        f"{len(preview.operation_summaries)} operations · {len(preview.paths)} paths",
    ]
    if preview.risk_flags:
        rows.append("risks: " + ", ".join(preview.risk_flags))
    available = max(0, max_rows - len(rows) - 4)
    rows.extend(
        clip_display("· " + safe_text(summary), columns)
        for summary in preview.operation_summaries[:available]
    )
    suffix = " · truncated" if preview.diff_truncated else ""
    rows.extend(
        (
            f"D review combined diff{suffix}",
            ("› " if choice == 0 else "  ") + "No",
            ("› " if choice == 1 else "  ") + "Yes",
            "Enter select · Esc cancel",
        )
    )
    return tuple(rows[:max_rows])


def handle_edit_plan_approval_key(
    request: ApprovalRequest, diff: DiffInteraction, key: str
) -> bool:
    preview = getattr(request, "edit_plan", None)
    if preview is None:
        return False
    if diff.active:
        if key in _DIFF_KEYS:
            diff.handle_key(key)
        return True
    if key.casefold() != "d":
        return False
    view = DiffView.parse(preview.combined_diff, DiffScope.PER_TURN, fresh=False)
    diff.open(
        view,
        scope=DiffScope.PER_TURN,
        recorded_diff=preview.combined_diff,
        paths=preview.paths,
    )
    return True


def close_edit_plan_approval_diff(diff: DiffInteraction) -> None:
    if diff.active:
        diff.finish_send()


__all__ = [
    "close_edit_plan_approval_diff",
    "edit_plan_approval_rows",
    "handle_edit_plan_approval_key",
]
