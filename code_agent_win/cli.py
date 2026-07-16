from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

from .app import create_application
from code_agent.interfaces.commands import CommandKind, execute_command, parse_command


async def run(arguments: Sequence[str]) -> int:
    try:
        mode_name, remaining = _split_mode_option(arguments)
        profile_name, model_name, command_arguments = _split_global_options(remaining)
        command = parse_command(command_arguments)
    except (TypeError, ValueError) as error:
        print(f"usage error: {error}", file=sys.stderr)
        return 2
    application = None
    try:
        application = create_application(
            model_name=model_name, profile_name=profile_name, mode_name=mode_name
        )
        application.dispatcher.interactive = command.kind is CommandKind.TUI
        return await execute_command(
            command,
            application.controller,
            application.tui,
            sys.stdout.write,
            application.foreground_tasks,
        )
    except Exception as error:
        print(f"agent error: {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        if application is not None:
            await application.aclose()


def main() -> int:
    return asyncio.run(run(sys.argv[1:]))


def _split_global_options(arguments: Sequence[str]) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Extract order-independent global profile options before command grammar."""
    values = tuple(arguments)
    if not all(isinstance(value, str) for value in values):
        raise TypeError("command arguments must be text")
    result: dict[str, str] = {}; command: list[str] = []; index = 0
    while index < len(values):
        item = values[index]
        if item not in {"--model", "--profile"}:
            command.append(item); index += 1; continue
        if item in result or index + 1 >= len(values) or not values[index + 1].strip():
            raise ValueError(f"{item} requires one non-blank value and may be specified once")
        result[item] = values[index + 1]; index += 2
    return result.get("--profile"), result.get("--model"), tuple(command)


def _split_mode_option(arguments: Sequence[str]) -> tuple[str | None, tuple[str, ...]]:
    values = tuple(arguments)
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
        if selected not in {"low", "medium", "high", "ultra"}:
            raise ValueError("--mode must be low, medium, high, or ultra")
        index += 2
    return selected, tuple(result)


if __name__ == "__main__":
    raise SystemExit(main())
