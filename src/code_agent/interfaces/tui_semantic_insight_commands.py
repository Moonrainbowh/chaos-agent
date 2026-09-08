from __future__ import annotations

import shlex
from typing import Any

from code_agent.context.errors import ContextError
from code_agent.workspace.errors import WorkspaceError

from .semantic_insight_view import format_semantic_insight
from .terminal_display import DisplayKind


async def handle_semantic_insight_command(
    app: Any,
    instruction: str | None,
    action: str | None,
) -> bool:
    control = getattr(app, "semantic_graph", None)
    if control is None:
        app._append(DisplayKind.ERROR, "semantic repository map is unavailable")
        return False
    try:
        arguments, options = _options(_arguments(instruction, action))
        app._append(DisplayKind.METADATA, "Refreshing the shared semantic graph; the first index may take a moment…")
        report = await control.analyze(
            action or "overview",
            arguments,
            thread_id=app.current_thread_id,
            **options,
        )
    except (ContextError, WorkspaceError, RuntimeError, TypeError, ValueError, OSError) as error:
        app._append(DisplayKind.ERROR, str(error)[:500])
        return False
    app._append(DisplayKind.METADATA, format_semantic_insight(report))
    return True


def _arguments(
    instruction: str | None, action: str | None
) -> tuple[str, ...]:
    if not instruction:
        return ()
    try:
        parts = tuple(shlex.split(instruction, posix=False))
    except ValueError as error:
        raise ValueError("invalid quoted map arguments") from error
    if action and parts:
        parts = parts[1:]
    return tuple(_unquote(value) for value in parts)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _options(values: tuple[str, ...]) -> tuple[tuple[str, ...], dict[str, int]]:
    arguments: list[str] = []
    options: dict[str, int] = {}
    literal = False
    for value in values:
        if value == "--" and not literal:
            literal = True
        elif value.startswith("--") and not literal:
            key, separator, raw = value[2:].partition("=")
            if key not in {"limit", "offset"} or not separator or key in options:
                raise ValueError("map options: --limit=1..50 --offset=0..100000; use -- before literal query flags")
            try:
                options[key] = int(raw)
            except ValueError as error:
                raise ValueError(f"map {key} must be an integer") from error
        else:
            arguments.append(value)
    return tuple(arguments), options
