from __future__ import annotations

import shlex
import subprocess
from typing import Any

from .terminal_display import DisplayKind


async def handle_permission_command(
    app: Any, instruction: str | None, action: str | None
) -> bool:
    if app.permissions is None:
        app._append(DisplayKind.ERROR, "permission controls are unavailable")
        return False
    if action in {"allow-command", "允许命令"}:
        return _allow_process(app, instruction)
    if action in {"rules", "规则"}:
        return _list_rules(app)
    if action in {"revoke", "撤销"}:
        return _revoke_rule(app, instruction)
    if instruction is None:
        current = app.permissions.current
        choices = " | ".join(item.name for item in app.permissions.list())
        app._append(DisplayKind.METADATA, f"current {current.name} | {choices}")
        return True
    try:
        selected = await app.permissions.use(
            action or instruction, idle=app._run_task is None or app._run_task.done()
        )
    except (ValueError, RuntimeError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(
        DisplayKind.METADATA,
        f"permission selected: {selected.name} · {selected.description}",
    )
    return True


def _allow_process(app: Any, instruction: str | None) -> bool:
    try:
        values = _payload(instruction)
        network = bool(values and values[0] == "--network")
        values = values[1:] if network else values
        if not values:
            raise ValueError("program is required")
        rule = app.permissions.allow_process(
            values[0], values[1:], network=network
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    command = subprocess.list2cmdline((rule.program_path, *rule.args))
    suffix = " · network" if rule.network else ""
    app._append(
        DisplayKind.METADATA,
        f"permanent command allowed: {rule.id[:8]} · {command}{suffix}",
    )
    return True


def _list_rules(app: Any) -> bool:
    try:
        rules = app.permissions.list_rules()
    except RuntimeError as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    if not rules:
        app._append(DisplayKind.METADATA, "no permanent command rules")
        return True
    rows = []
    for rule in rules:
        command = subprocess.list2cmdline((rule.program_path, *rule.args))
        suffix = " [network]" if rule.network else ""
        rows.append(f"{rule.id[:8]} · {command}{suffix}")
    app._append(DisplayKind.METADATA, "\n".join(rows))
    return True


def _revoke_rule(app: Any, instruction: str | None) -> bool:
    try:
        values = _payload(instruction)
        if len(values) != 1:
            raise ValueError("one rule id or unique prefix is required")
        rule = app.permissions.revoke_rule(values[0])
    except (KeyError, RuntimeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(DisplayKind.METADATA, f"permission rule revoked: {rule.id[:8]}")
    return True


def _payload(instruction: str | None) -> tuple[str, ...]:
    if not isinstance(instruction, str) or not instruction.strip():
        return ()
    values = tuple(_unquote(value) for value in shlex.split(instruction, posix=False))
    return values[1:]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


__all__ = ("handle_permission_command",)
