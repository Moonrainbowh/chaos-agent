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
        if action in {"列表", "list"}:
            values = app.skills.list()
            app._append(
                DisplayKind.METADATA,
                " | ".join(item.identifier for item in values) or "No Skills",
            )
        elif action in {"信息", "info"} and identifier:
            skill = app.skills.info(identifier)
            app._append(
                DisplayKind.METADATA,
                f"{skill.identifier} · {skill.description} · {skill.digest}",
            )
        elif action in {"来源", "source"} and identifier:
            app._append(
                DisplayKind.METADATA,
                " | ".join(app.skills.sources(identifier)),
            )
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
    except (KeyError, PermissionError, RuntimeError, ValueError) as error:
        app._append(DisplayKind.ERROR, type(error).__name__)
        return False
    return True
