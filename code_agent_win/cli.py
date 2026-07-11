from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

from .app import create_application
from code_agent.interfaces.commands import CommandKind, execute_command, parse_command


async def run(arguments: Sequence[str]) -> int:
    try:
        command = parse_command(arguments)
    except (TypeError, ValueError) as error:
        print(f"usage error: {error}", file=sys.stderr)
        return 2
    application = None
    try:
        application = create_application()
        application.dispatcher.interactive = command.kind is CommandKind.TUI
        return await execute_command(
            command,
            application.controller,
            application.tui,
            sys.stdout.write,
        )
    except Exception as error:
        print(f"agent error: {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        if application is not None:
            await application.aclose()


def main() -> int:
    return asyncio.run(run(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
