from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from code_agent.core.models import Message

from .models import CompactionResult, ContextConfig
from .attachment_budget import message_tokens
from ._compaction_render import _last_resort, _summary, _truncate_message
from .errors import ContextBudgetError


@dataclass(frozen=True)
class _MessageBlock:
    indices: tuple[int, ...]
    messages: tuple[Message, ...]
    valid: bool = True


class DeterministicCompactor:
    """Compact old messages without model calls or input mutation."""

    def __init__(self, config: ContextConfig) -> None:
        if not isinstance(config, ContextConfig):
            raise TypeError("config must be a ContextConfig")
        self.config = config

    def compact(
        self,
        messages: Sequence[Message],
        token_budget: int | None = None,
    ) -> CompactionResult:
        checked, budget = _validated_input(
            messages, self.config.message_tokens, token_budget
        )
        original_cost = _messages_cost(checked)
        if original_cost <= budget:
            return CompactionResult(checked, 0, original_cost)
        return _compact_over_budget(
            checked, budget, self.config.recent_messages
        )


def _validated_input(
    messages: Sequence[Message],
    default_budget: int,
    supplied_budget: int | None,
) -> tuple[tuple[Message, ...], int]:
    checked = tuple(messages)
    if not all(isinstance(message, Message) for message in checked):
        raise TypeError("messages must contain only Message values")
    budget = default_budget if supplied_budget is None else supplied_budget
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise TypeError("token_budget must be an integer")
    if budget <= 0:
        raise ValueError("token_budget must be positive")
    return checked, budget


def _compact_over_budget(
    checked: tuple[Message, ...], budget: int, recent_messages: int
) -> CompactionResult:
    blocks = _message_blocks(checked)
    selected = _select_recent_blocks(blocks, checked, recent_messages)
    latest_user = _latest_user_index(checked)
    _ensure_latest_attachment_budget(checked, latest_user, budget)
    protected = _block_containing(blocks, latest_user)
    if protected is None and selected:
        protected = max(selected)
    block_messages = {
        index: block.messages for index, block in enumerate(blocks)
    }
    _drop_old_blocks(
        blocks, block_messages, selected, protected, len(checked), budget
    )
    retained, removed = _fit_retained(
        checked, blocks, block_messages, selected, protected, latest_user, budget
    )
    compacted, summary = _prepend_summary(checked, retained, removed, budget)
    estimated = _messages_cost(compacted)
    if estimated > budget:
        compacted, summary = _last_resort(
            compacted, checked, latest_user, budget
        )
        estimated = _messages_cost(compacted)
    return CompactionResult(compacted, len(removed), estimated, summary)


def _ensure_latest_attachment_budget(
    messages: tuple[Message, ...], latest_user: int | None, budget: int
) -> None:
    if latest_user is None or not messages[latest_user].attachments:
        return
    metadata_only = Message(
        role="user", attachments=messages[latest_user].attachments
    )
    if _message_cost(metadata_only) > budget:
        raise ContextBudgetError(
            "latest user attachments exceed the message token budget"
        )


def _drop_old_blocks(
    blocks: tuple[_MessageBlock, ...],
    block_messages: dict[int, tuple[Message, ...]],
    selected: set[int],
    protected: int | None,
    message_count: int,
    budget: int,
) -> None:
    while True:
        retained = _flatten_selected(block_messages, selected)
        removed = _removed_indices(blocks, selected, message_count)
        reserve = 1 if removed and budget >= 2 else 0
        if _messages_cost(retained) + reserve <= budget:
            return
        protected_set = {protected} if protected is not None else set()
        removable = sorted(selected - protected_set)
        if not removable:
            return
        selected.remove(removable[0])


def _fit_retained(
    messages: tuple[Message, ...],
    blocks: tuple[_MessageBlock, ...],
    block_messages: dict[int, tuple[Message, ...]],
    selected: set[int],
    protected: int | None,
    latest_user: int | None,
    budget: int,
) -> tuple[tuple[Message, ...], tuple[int, ...]]:
    retained = _flatten_selected(block_messages, selected)
    removed = _removed_indices(blocks, selected, len(messages))
    reserve = 1 if removed and budget >= 2 else 0
    if _messages_cost(retained) + reserve <= budget:
        return retained, removed
    if protected is not None and protected in selected and latest_user is not None:
        truncated = _truncate_message(messages[latest_user], budget - reserve)
        retained = (
            (truncated,) if truncated is not None else (Message("user"),)
        )
        selected.intersection_update({protected})
    else:
        retained = ()
        selected.clear()
    return retained, _removed_indices(blocks, selected, len(messages))


def _prepend_summary(
    messages: tuple[Message, ...],
    retained: tuple[Message, ...],
    removed: tuple[int, ...],
    budget: int,
) -> tuple[tuple[Message, ...], str | None]:
    remaining = budget - _messages_cost(retained)
    if not removed or remaining < 1:
        return retained, None
    summary = _summary(messages, removed, max(0, remaining - 1))
    return (Message(role="developer", content=summary),) + retained, summary


def _message_cost(message: Message) -> int:
    return message_tokens(message)


def _messages_cost(messages: Sequence[Message]) -> int:
    return sum(_message_cost(message) for message in messages)


def _message_blocks(messages: Sequence[Message]) -> tuple[_MessageBlock, ...]:
    blocks: list[_MessageBlock] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "assistant" and message.tool_calls:
            expected = {call.id for call in message.tool_calls}
            end = index + 1
            while (
                end < len(messages)
                and messages[end].role == "tool"
                and messages[end].tool_call_id in expected
            ):
                end += 1
            blocks.append(
                _MessageBlock(
                    tuple(range(index, end)), tuple(messages[index:end])
                )
            )
            index = end
            continue
        blocks.append(
            _MessageBlock((index,), (message,), valid=message.role != "tool")
        )
        index += 1
    return tuple(blocks)


def _select_recent_blocks(
    blocks: Sequence[_MessageBlock],
    messages: Sequence[Message],
    recent_messages: int,
) -> set[int]:
    selected: set[int] = set()
    retained_count = 0
    for index in range(len(blocks) - 1, -1, -1):
        block = blocks[index]
        if not block.valid:
            continue
        selected.add(index)
        retained_count += len(block.messages)
        if retained_count >= recent_messages:
            break
    latest_user = _latest_user_index(messages)
    containing = _block_containing(blocks, latest_user)
    if containing is not None and blocks[containing].valid:
        selected.add(containing)
    return selected


def _latest_user_index(messages: Sequence[Message]) -> int | None:
    return next(
        (index for index in range(len(messages) - 1, -1, -1) if messages[index].role == "user"),
        None,
    )


def _block_containing(
    blocks: Sequence[_MessageBlock], message_index: int | None
) -> int | None:
    if message_index is None:
        return None
    return next(
        (index for index, block in enumerate(blocks) if message_index in block.indices),
        None,
    )


def _flatten_selected(
    block_messages: dict[int, tuple[Message, ...]], selected: set[int]
) -> tuple[Message, ...]:
    return tuple(
        message
        for index in sorted(selected)
        for message in block_messages[index]
    )


def _removed_indices(
    blocks: Sequence[_MessageBlock], selected: set[int], message_count: int
) -> tuple[int, ...]:
    retained = {
        message_index
        for block_index in selected
        for message_index in blocks[block_index].indices
    }
    return tuple(index for index in range(message_count) if index not in retained)
