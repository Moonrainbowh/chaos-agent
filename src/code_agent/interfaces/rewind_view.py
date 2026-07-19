from __future__ import annotations

from datetime import datetime, timezone

from .rewind_models import (
    RewindCheckpointPage,
    RewindDisabledReason,
    RewindFacts,
    RewindKind,
    RewindPreview,
)
from .terminal_display import safe_text


MAX_REWIND_RENDER_ROWS = 20
_GLOBAL_REASONS = frozenset(
    {
        RewindDisabledReason.CHECKPOINT_NOT_FOUND,
        RewindDisabledReason.SOURCE_CHANGED_DURING_PREVIEW,
    }
)
_CONVERSATION_REASONS = _GLOBAL_REASONS | frozenset(
    {
        RewindDisabledReason.MESSAGE_BOUND_MISSING,
        RewindDisabledReason.MESSAGE_BOUND_INVALID,
    }
)


def _validate_row_limit(value: object, field: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field} must be an integer")
    if not 0 <= value <= MAX_REWIND_RENDER_ROWS:
        raise ValueError(
            f"{field} must be between 0 and {MAX_REWIND_RENDER_ROWS}"
        )
    return value


def _single_line(value: object) -> str:
    return " ".join(safe_text(value).splitlines())


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _observation(value: int | str | None) -> str:
    return "-" if value is None else str(value)


def _selected_reasons(
    kind: RewindKind, facts: RewindFacts
) -> tuple[RewindDisabledReason, ...]:
    selected = set()
    if kind in (RewindKind.CONVERSATION, RewindKind.BOTH):
        selected.add(facts.conversation_disabled_reason)
    if kind in (RewindKind.CODE, RewindKind.BOTH):
        selected.add(facts.code_disabled_reason)
    selected.discard(None)
    return tuple(reason for reason in RewindDisabledReason if reason in selected)


def build_rewind_preview(
    kind: RewindKind, facts: RewindFacts
) -> RewindPreview:
    """Project immutable source facts into a read-only facet preview."""
    if type(kind) is not RewindKind:
        raise TypeError("kind must be a RewindKind")
    if type(facts) is not RewindFacts:
        raise TypeError("facts must be RewindFacts")
    reasons = _selected_reasons(kind, facts)
    enabled = not reasons
    includes_conversation = kind in (
        RewindKind.CONVERSATION,
        RewindKind.BOTH,
    )
    includes_code = kind in (RewindKind.CODE, RewindKind.BOTH)
    return RewindPreview(
        kind=kind,
        checkpoint_id=facts.checkpoint_id,
        checkpoint_label=facts.checkpoint_label,
        as_of=facts.as_of,
        conversation_messages=(
            facts.conversation_messages if includes_conversation else 0
        ),
        code_paths=facts.code_paths if includes_code else (),
        disabled_reasons=reasons,
        enabled=enabled,
        requires_confirmation=enabled,
        apply_available=False,
        requires_git_reset=False,
    )


def _as_of_line(preview: RewindPreview) -> str:
    observed = preview.as_of
    fields = (
        ("message", observed.message_sequence),
        ("event", observed.event_sequence),
        ("mutation", observed.mutation_sequence),
        ("coverage", observed.coverage_generation),
        ("paths", observed.relevant_path_digest),
    )
    details = " · ".join(
        f"{label}={_observation(value)}" for label, value in fields
    )
    return f"as of: {_utc_text(observed.captured_at)} · {details}"


def _conversation_value(preview: RewindPreview) -> str:
    if preview.kind is RewindKind.CODE:
        return "unavailable"
    if any(reason in _CONVERSATION_REASONS for reason in preview.disabled_reasons):
        return "unavailable"
    return str(preview.conversation_messages)


def _path_lines(preview: RewindPreview, limit: int) -> list[str]:
    lines = [
        (
            f"- {_single_line(item.path)}"
            f" · baseline {_single_line(item.baseline_provenance)}"
            " · preserves pre-agent baseline"
        )
        for item in preview.code_paths[:limit]
    ]
    hidden = len(preview.code_paths) - limit
    if hidden > 0:
        lines.append(f"... {hidden} paths hidden")
    return lines


def render_rewind_preview(
    preview: RewindPreview, *, max_path_rows: int = 20
) -> str:
    """Render a bounded preview without emitting trusted terminal controls."""
    if type(preview) is not RewindPreview:
        raise TypeError("preview must be a RewindPreview")
    limit = _validate_row_limit(max_path_rows, "max_path_rows")
    preserved = sum(
        path.preserves_pre_agent_baseline for path in preview.code_paths
    )
    state = "state: enabled"
    if preview.disabled_reasons:
        reasons = ", ".join(reason.value for reason in preview.disabled_reasons)
        state = f"state: disabled · {reasons}"
    lines = [
        "rewind · preview only",
        (
            f"checkpoint: {_single_line(preview.checkpoint_id)}"
            f" · {_single_line(preview.checkpoint_label)}"
        ),
        f"kind: {preview.kind.value}",
        _as_of_line(preview),
        f"conversation messages: {_conversation_value(preview)}",
        f"code paths: {len(preview.code_paths)}",
        f"pre-agent baselines preserved: {preserved}",
        *_path_lines(preview, limit),
        state,
        (
            "confirmation required: yes"
            if preview.requires_confirmation
            else "confirmation required: no"
        ),
        "apply unavailable",
        "no git reset",
    ]
    return "\n".join(lines)


def render_rewind_candidates(
    page: RewindCheckpointPage, *, max_items: int = 20
) -> str:
    """Render a bounded checkpoint page while preserving its opaque cursor."""
    if type(page) is not RewindCheckpointPage:
        raise TypeError("page must be a RewindCheckpointPage")
    limit = _validate_row_limit(max_items, "max_items")
    lines = [
        "rewind · candidate only",
        f"checkpoint candidates: {len(page.items)}",
    ]
    for item in page.items[:limit]:
        lines.append(
            f"- {_single_line(item.checkpoint_id)}"
            f" · {_utc_text(item.created_at)}"
            f" · {_single_line(item.label)}"
            f" · message-bound {'yes' if item.has_message_bound else 'no'}"
            f" · code anchor {'yes' if item.has_code_anchor else 'no'}"
        )
    hidden = len(page.items) - limit
    if hidden > 0:
        lines.append(f"... {hidden} candidates hidden")
    cursor = "-" if page.next_cursor is None else _single_line(page.next_cursor)
    lines.append(f"opaque next cursor: {cursor}")
    return "\n".join(lines)


__all__ = [
    "MAX_REWIND_RENDER_ROWS",
    "build_rewind_preview",
    "render_rewind_candidates",
    "render_rewind_preview",
]
