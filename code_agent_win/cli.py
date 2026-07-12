from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

from .app import create_application
from code_agent.interfaces.commands import CommandKind, execute_command, parse_command


async def run(arguments: Sequence[str]) -> int:
    try:
        profile_name, command_arguments = _split_profile_option(arguments)
        model_name, command_arguments = _split_global_options(command_arguments)
        command = parse_command(command_arguments)
    except (TypeError, ValueError) as error:
        print(f"usage error: {error}", file=sys.stderr)
        return 2
    application = None
    try:
        application = create_application(
            model_name=model_name, profile_name=profile_name
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


def _split_global_options(arguments: Sequence[str]) -> tuple[str | None, tuple[str, ...]]:
    """Extract only leading CLI-wide options before handing off command grammar."""
    values = tuple(arguments)
    if not all(isinstance(value, str) for value in values):
        raise TypeError("command arguments must be text")
    if not values or values[0] != "--model":
        return None, values
    if len(values) < 2 or not values[1].strip():
        raise ValueError("--model requires a non-blank name")
    if len(values) > 2 and values[2] == "--model":
        raise ValueError("--model may be specified once")
    return values[1], values[2:]


def _split_profile_option(arguments: Sequence[str]) -> tuple[str | None, tuple[str, ...]]:
    """Extract the optional leading local configuration profile selection."""
    values = tuple(arguments)
    if not all(isinstance(value, str) for value in values):
        raise TypeError("command arguments must be text")
    if not values or values[0] != "--profile":
        return None, values
    if len(values) < 2 or not values[1].strip():
        raise ValueError("--profile requires a non-blank name")
    if len(values) > 2 and values[2] == "--profile":
        raise ValueError("--profile may be specified once")
    return values[1], values[2:]


if __name__ == "__main__":
    raise SystemExit(main())
