from __future__ import annotations

from datetime import datetime

from code_agent.interfaces.rewind_models import (
    RewindAsOf, RewindCheckpointCandidate, RewindCheckpointPage,
    RewindDisabledReason, RewindFacts, RewindPath,
)
from code_agent.sessions.rewind_models import RewindCandidatePage, RewindObservation


def candidate_page(source: RewindCandidatePage) -> RewindCheckpointPage:
    return RewindCheckpointPage(
        tuple(
            RewindCheckpointCandidate(
                item.checkpoint_id, item.label, item.created_at,
                item.has_message_bound, item.has_code_anchor,
            )
            for item in source.items
        ),
        source.next_cursor,
    )


def conversation_projection(
    observation: RewindObservation,
) -> tuple[int, RewindDisabledReason | None]:
    bound = observation.checkpoint.message_sequence
    if bound is None:
        return 0, RewindDisabledReason.MESSAGE_BOUND_MISSING
    count = observation.conversation_message_count
    if count is None or bound > observation.heads.message_sequence:
        return 0, RewindDisabledReason.MESSAGE_BOUND_INVALID
    return count, None


def ordinary_facts(
    observation: RewindObservation,
    captured_at: datetime,
    *,
    conversation_messages: int,
    conversation_reason: RewindDisabledReason | None,
    code_paths: tuple[RewindPath, ...],
    code_reason: RewindDisabledReason | None,
    digest: str | None,
    include_code_heads: bool,
) -> RewindFacts:
    heads = observation.heads
    return RewindFacts(
        observation.checkpoint.id,
        observation.checkpoint.label,
        observation.checkpoint.created_at,
        RewindAsOf(
            heads.message_sequence,
            heads.event_sequence,
            heads.mutation_sequence if include_code_heads else None,
            heads.coverage_generation if include_code_heads else None,
            digest,
            captured_at,
        ),
        conversation_messages,
        code_paths,
        conversation_reason,
        code_reason,
    )


def global_facts(
    checkpoint_id: str,
    label: str,
    reason: RewindDisabledReason,
    timestamp: datetime,
) -> RewindFacts:
    return RewindFacts(
        checkpoint_id,
        label,
        timestamp,
        RewindAsOf(None, None, None, None, None, timestamp),
        0,
        (),
        reason,
        reason,
    )


def stable_conversation(
    first: RewindObservation, second: RewindObservation
) -> bool:
    return (
        first.checkpoint == second.checkpoint
        and first.heads.message_sequence == second.heads.message_sequence
        and first.heads.event_sequence == second.heads.event_sequence
        and first.conversation_message_count == second.conversation_message_count
    )


def stable_full(first: RewindObservation, second: RewindObservation) -> bool:
    return (
        stable_conversation(first, second)
        and first.checkpoint_fact == second.checkpoint_fact
        and first.heads == second.heads
    )
