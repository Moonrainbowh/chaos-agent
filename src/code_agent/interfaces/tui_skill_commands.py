from __future__ import annotations

from typing import Any

from .terminal_display import DisplayKind


async def handle_skill_command(app: Any, instruction: str | None) -> bool:
    if getattr(app, "skills", None) is None:
        app._append(DisplayKind.ERROR, "Skills are unavailable")
        return False
    thread_id = getattr(app, "current_thread_id", None)
    action, _, identifier = (instruction or "列表").partition(" ")
    try:
        return await _dispatch_skill_command(app, action, identifier, thread_id)
    except (KeyError, PermissionError, RuntimeError, ValueError) as error:
        app._append(DisplayKind.ERROR, type(error).__name__)
        return False


async def _dispatch_skill_command(
    app: Any, action: str, identifier: str, thread_id: str | None
) -> bool:
    if action in {"列表", "list"}:
        return _render_skill_list(app, identifier, thread_id)
    if action in {"信息", "info"} and identifier:
        skill = app.skills.info(identifier)
        value = f"{skill.identifier} · {skill.description} · {skill.digest}"
        app._append(DisplayKind.METADATA, value)
    elif action in {"来源", "source"} and identifier:
        app._append(DisplayKind.METADATA, " | ".join(app.skills.sources(identifier)))
    elif action in {"启用", "enable"} and identifier and thread_id:
        await app.skills.enable(thread_id, identifier)
        app._append(DisplayKind.METADATA, f"Skill enabled: {identifier}")
    elif action in {"禁用", "disable"} and identifier and thread_id:
        await app.skills.disable(thread_id, identifier)
        app._append(DisplayKind.METADATA, f"Skill disabled: {identifier}")
    elif action in {"重载", "reload"}:
        await app.skills.reload(thread_id)
        app._append(DisplayKind.METADATA, "Skills reloaded")
    else:
        app._append(DisplayKind.ERROR, "Skill command arguments are invalid")
        return False
    return True


def _render_skill_list(app: Any, identifier: str, thread_id: str | None) -> bool:
    if identifier in {"", "--all"}:
        values = app.skills.list()
        empty = "No Skills"
    elif identifier == "--active" and thread_id:
        values = app.skills.activation(thread_id).active()
        empty = "No active Skills"
    elif identifier == "--errors":
        values = app.skills.errors()
        empty = "No Skill errors"
    else:
        app._append(DisplayKind.ERROR, "Skill list filter is invalid")
        return False
    rendered = " | ".join(getattr(item, "identifier", str(item)) for item in values)
    app._append(DisplayKind.METADATA, rendered or empty)
    return True
