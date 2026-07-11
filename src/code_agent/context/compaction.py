from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from code_agent.core.models import Message

from .models import CompactionResult, ContextConfig
from .tokens import estimate_tokens, truncate_to_tokens


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
        checked = tuple(messages)
        if not all(isinstance(message, Message) for message in checked):
            raise TypeError("messages must contain only Message values")
        budget = self.config.message_tokens if token_budget is None else token_budget
        if isinstance(budget, bool) or not isinstance(budget, int):
            raise TypeError("token_budget must be an integer")
        if budget <= 0:
            raise ValueError("token_budget must be positive")

        original_cost = _messages_cost(checked)
        if original_cost <= budget:
            return CompactionResult(checked, 0, original_cost)

        blocks = _message_blocks(checked)
        selected = _select_recent_blocks(
            blocks, checked, self.config.recent_messages
        )
        latest_user = _latest_user_index(checked)
        protected = _block_containing(blocks, latest_user)
        if protected is None and selected:
            protected = max(selected)
        block_messages = {index: block.messages for index, block in enumerate(blocks)}

        while True:
            retained = _flatten_selected(block_messages, selected)
            removed = _removed_indices(blocks, selected, len(checked))
            reserve = 1 if removed and budget >= 2 else 0
            if _messages_cost(retained) + reserve <= budget:
                break
            removable = sorted(selected - ({protected} if protected is not None else set()))
            if not removable:
                break
            selected.remove(removable[0])

        retained = _flatten_selected(block_messages, selected)
        removed = _removed_indices(blocks, selected, len(checked))
        reserve = 1 if removed and budget >= 2 else 0
        if _messages_cost(retained) + reserve > budget:
            if protected is not None and protected in selected and latest_user is not None:
                source = checked[latest_user]
                truncated = _truncate_message(source, budget - reserve)
                retained = (truncated,) if truncated is not None else (
                    Message(role="user", content=""),
                )
                selected = {protected}
            else:
                retained = ()
                selected.clear()
            removed = _removed_indices(blocks, selected, len(checked))

        checkpoint: Message | None = None
        summary: str | None = None
        remaining = budget - _messages_cost(retained)
        if removed and remaining >= 1:
            summary = _summary(checked, removed, max(0, remaining - 1))
            checkpoint = Message(role="developer", content=summary)

        compacted = ((checkpoint,) if checkpoint is not None else ()) + retained
        estimated = _messages_cost(compacted)
        if estimated > budget:
            compacted, summary = _last_resort(compacted, checked, latest_user, budget)
            estimated = _messages_cost(compacted)
        return CompactionResult(
            compacted,
            len(removed),
            estimated,
            summary,
        )


def _message_cost(message: Message) -> int:
    cost = 1 + estimate_tokens(message.content)
    if message.name:
        cost += estimate_tokens(message.name)
    if message.tool_call_id:
        cost += estimate_tokens(message.tool_call_id)
    for call in message.tool_calls:
        encoded = json.dumps(
            call.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        cost += 1 + estimate_tokens(encoded)
    return cost


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


def _truncate_message(message: Message, budget: int) -> Message | None:
    if budget < 1:
        return None
    empty = Message(
        role=message.role,
        content="",
        name=message.name,
        tool_calls=message.tool_calls,
        tool_call_id=message.tool_call_id,
    )
    base = _message_cost(empty)
    if base > budget:
        return None
    return Message(
        role=message.role,
        content=truncate_to_tokens(message.content, budget - base),
        name=message.name,
        tool_calls=message.tool_calls,
        tool_call_id=message.tool_call_id,
    )


def _summary(
    messages: Sequence[Message], removed: Sequence[int], content_budget: int
) -> str:
    if content_budget == 0:
        return ""
    lines = ["Conversation checkpoint:"]
    for index in removed:
        message = messages[index]
        actions = ",".join(call.name for call in message.tool_calls)
        if not actions and message.role == "tool":
            actions = message.name or message.tool_call_id or "tool"
        label = message.role + (f" action={actions}" if actions else "")
        content = " ".join(message.content.split())
        snippet = truncate_to_tokens(content, 12)
        lines.append(f"- {label}: {snippet}" if snippet else f"- {label}")
    return truncate_to_tokens("\n".join(lines), content_budget)


def _last_resort(
    compacted: Sequence[Message],
    original: Sequence[Message],
    latest_user: int | None,
    budget: int,
) -> tuple[tuple[Message, ...], str | None]:
    if latest_user is not None:
        message = _truncate_message(original[latest_user], budget)
        if message is not None:
            return (message,), None
    for message in reversed(compacted):
        if message.role != "tool":
            truncated = _truncate_message(message, budget)
            if truncated is not None:
                return (truncated,), None
    return (), None
