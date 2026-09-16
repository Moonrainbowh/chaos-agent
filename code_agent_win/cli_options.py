"""Parse the global options that may precede any Chaos Agent command.

Option splitting lives here rather than in ``cli`` so the command grammar,
the meta commands, and the option tables each stay readable. Every splitter
returns the values it consumed plus the arguments that belong to the command,
which is what lets options and commands appear in any order.
"""

from __future__ import annotations

from collections.abc import Sequence


MODE_VALUES = ("low", "medium", "high", "ultra")
ISOLATION_FLAG = "--isolated"
RECLAIM_FLAG = "--reclaim-workspaces"
_TYPED = ("command arguments must be text")


def _require_text(arguments: Sequence[str]) -> tuple[str, ...]:
    values = tuple(arguments)
    if not all(isinstance(value, str) for value in values):
        raise TypeError(_TYPED)
    return values


def _split_global_options(
    arguments: Sequence[str],
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Extract order-independent global profile options before command grammar."""
    values = _require_text(arguments)
    result: dict[str, str] = {}; command: list[str] = []; index = 0
    while index < len(values):
        item = values[index]
        if item not in {"--model", "--profile"}:
            command.append(item); index += 1; continue
        if item in result or index + 1 >= len(values) or not values[index + 1].strip():
            raise ValueError(f"{item} requires one non-blank value and may be specified once")
        result[item] = values[index + 1]; index += 2
    return result.get("--profile"), result.get("--model"), tuple(command)


def _split_mode_option(
    arguments: Sequence[str],
) -> tuple[str | None, tuple[str, ...]]:
    values = _require_text(arguments)
    result: list[str] = []
    selected: str | None = None
    index = 0
    while index < len(values):
        if values[index] != "--mode":
            result.append(values[index])
            index += 1
            continue
        if selected is not None or index + 1 >= len(values):
            raise ValueError("--mode requires one value and may be specified once")
        selected = values[index + 1]
        if selected not in MODE_VALUES:
            raise ValueError("--mode must be low, medium, high, or ultra")
        index += 2
    return selected, tuple(result)


def _split_attachment_options(
    arguments: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    values = _require_text(arguments)
    paths: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(values):
        if values[index] != "--attach":
            remaining.append(values[index])
            index += 1
            continue
        if index + 1 >= len(values) or not values[index + 1].strip():
            raise ValueError("--attach requires one non-blank path")
        paths.append(values[index + 1])
        index += 2
    return tuple(paths), tuple(remaining)


def _split_flag(
    arguments: Sequence[str], flag: str
) -> tuple[bool, tuple[str, ...]]:
    """Strip one repeatable-once boolean flag before command grammar."""
    values = _require_text(arguments)
    if values.count(flag) > 1:
        raise ValueError(f"{flag} may be specified once")
    return flag in values, tuple(value for value in values if value != flag)


def _split_isolation_option(
    arguments: Sequence[str],
) -> tuple[bool, tuple[str, ...]]:
    """Ask for managed Git worktree isolation for this invocation.

    The flag keeps "run this in isolation" a per-task choice instead of a
    process-wide ``CHAOS_WORKSPACE_MODE`` default.
    """
    return _split_flag(arguments, ISOLATION_FLAG)


def _split_reclaim_option(
    arguments: Sequence[str],
) -> tuple[bool, tuple[str, ...]]:
    """Ask to retire managed worktrees that hold no work, then exit."""
    return _split_flag(arguments, RECLAIM_FLAG)


__all__ = [
    "ISOLATION_FLAG",
    "MODE_VALUES",
    "RECLAIM_FLAG",
    "_split_attachment_options",
    "_split_global_options",
    "_split_isolation_option",
    "_split_mode_option",
    "_split_reclaim_option",
]
