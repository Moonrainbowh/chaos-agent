from __future__ import annotations

import shlex
from dataclasses import dataclass

from .rewind_models import RewindKind, RewindPreviewSource
from .rewind_view import render_rewind_candidates, render_rewind_preview
from .terminal_display import DisplayKind


_USAGE = (
    "rewind expects list [cursor] or preview "
    "<checkpoint-id> <conversation|code|both>"
)


@dataclass(frozen=True)
class RewindCommandResult:
    handled: bool
    display_kind: DisplayKind
    text: str


def _result(kind: DisplayKind, text: str) -> RewindCommandResult:
    return RewindCommandResult(True, kind, text)


def _parse_instruction(instruction: str | None) -> list[str] | None:
    if not isinstance(instruction, str):
        return None
    try:
        return shlex.split(instruction)
    except ValueError:
        return None


async def _list_candidates(
    source: RewindPreviewSource,
    thread_id: str,
    arguments: list[str],
) -> RewindCommandResult:
    if len(arguments) > 1:
        return _result(
            DisplayKind.ERROR, "rewind list expects at most one cursor"
        )
    cursor = arguments[0] if arguments else None
    page = await source.list_candidates(
        thread_id, cursor=cursor, limit=20
    )
    return _result(
        DisplayKind.METADATA, render_rewind_candidates(page)
    )


async def _preview(
    source: RewindPreviewSource,
    thread_id: str,
    arguments: list[str],
) -> RewindCommandResult:
    if len(arguments) != 2:
        return _result(
            DisplayKind.ERROR,
            "rewind preview expects <checkpoint-id> "
            "<conversation|code|both>",
        )
    checkpoint_id, kind_value = arguments
    try:
        kind = RewindKind(kind_value)
    except ValueError:
        return _result(
            DisplayKind.ERROR,
            "rewind kind must be conversation, code, or both",
        )
    preview = await source.preview(thread_id, checkpoint_id, kind)
    return _result(DisplayKind.METADATA, render_rewind_preview(preview))


async def handle_rewind_command(
    source: RewindPreviewSource,
    thread_id: str | None,
    instruction: str | None,
) -> RewindCommandResult:
    """Parse and delegate a read-only rewind list or preview request."""
    if not thread_id:
        return _result(
            DisplayKind.ERROR, "rewind requires a current thread"
        )
    parts = _parse_instruction(instruction)
    if not parts:
        return _result(DisplayKind.ERROR, _USAGE)
    action, arguments = parts[0], parts[1:]
    try:
        if action in {"list", "列表"}:
            return await _list_candidates(source, thread_id, arguments)
        if action in {"preview", "预览"}:
            return await _preview(source, thread_id, arguments)
        return _result(DisplayKind.ERROR, _USAGE)
    except Exception:
        return _result(
            DisplayKind.ERROR, "rewind source is unavailable"
        )


__all__ = ["RewindCommandResult", "handle_rewind_command"]
