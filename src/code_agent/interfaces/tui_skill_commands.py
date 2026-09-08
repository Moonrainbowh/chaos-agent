from __future__ import annotations

from typing import Any

from .terminal_display import DisplayKind, clip_display, display_width


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
    if action in {"运行", "run", "use"} and identifier:
        skill_id, _, prompt = identifier.partition(" ")
        skill_id = skill_id.strip()
        prompt = prompt.strip()
        if thread_id:
            await app.skills.enable(thread_id, skill_id)
        skill = app.skills.info(skill_id)
        desc = (getattr(skill, "description", "") or "").split("\n")[0]
        if prompt:
            app._append(DisplayKind.METADATA, f"Skill [{skill_id}] active · {desc}")
            await app.submit(prompt)
        else:
            app._append(
                DisplayKind.METADATA,
                f"Skill [{skill_id}] enabled · {desc}\n  Ready. Run /{skill_id} <prompt> or chat directly.",
            )
    elif action in {"信息", "info"} and identifier:
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
    if identifier == "":
        return _render_structured_skill_list(app, thread_id)
    if identifier == "--all":
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


def _render_structured_skill_list(app: Any, thread_id: str | None) -> bool:
    values = app.skills.list()
    if not values:
        app._append(DisplayKind.METADATA, "No skills available")
        return True

    active_ids: set[str] = set()
    if thread_id and hasattr(app.skills, "activation"):
        try:
            active_ids = {s.identifier for s in app.skills.activation(thread_id).active()}
        except Exception:
            pass

    categories: dict[str, list[Any]] = {
        "Academic & Research (科研与文献)": [],
        "Content & Growth (内容与校准)": [],
        "Engineering & ML (研发与实验)": [],
        "Office & Presentation (设计与办公)": [],
        "Thinking & Strategy (思考与工作流)": [],
        "Tools & Utilities (工具集成与其它)": [],
    }

    for skill in values:
        ident = getattr(skill, "identifier", str(skill))
        if any(ident.startswith(p) for p in ("academic-", "nature-", "wos-", "geo-ml", "deep-research", "instsci")):
            categories["Academic & Research (科研与文献)"].append(skill)
        elif any(ident.startswith(p) for p in ("cheat-", "xiaoyuzhou")):
            categories["Content & Growth (内容与校准)"].append(skill)
        elif any(ident.startswith(p) for p in ("exp-", "git-commit", "playwright", "neat-freak", "pua")):
            categories["Engineering & ML (研发与实验)"].append(skill)
        elif any(ident.startswith(p) for p in ("dashi-ppt", "pptx", "slides", "docx", "spreadsheet", "xlsx", "drawio", "banner-design", "design", "ui-", "frontend")):
            categories["Office & Presentation (设计与办公)"].append(skill)
        elif any(ident.startswith(p) for p in ("brainstorming", "overall-planning", "practice-cognition", "personal-thinking", "workflows", "finding-unknowns", "investigation-first", "mass-line", "protracted-strategy", "spark-prairie", "qiushi")):
            categories["Thinking & Strategy (思考与工作流)"].append(skill)
        else:
            categories["Tools & Utilities (工具集成与其它)"].append(skill)

    columns = getattr(app, "_columns", lambda: 100)()
    lines = [
        f"Installed Skills ({len(values)} total · Direct slash: /<skill-name> [prompt]):",
    ]

    for cat_name, cat_skills in categories.items():
        if not cat_skills:
            continue
        lines.append(f"\n  [{cat_name}]")
        for s in cat_skills:
            ident = getattr(s, "identifier", str(s))
            is_active = ident in active_ids
            status = "●" if is_active else "○"
            left = f"    {status} /{ident:<26}"
            desc = getattr(s, "description", "") or ""
            first_line = desc.strip().split("\n")[0] if desc else ""
            avail = max(10, columns - display_width(left) - 6)
            clipped_desc = clip_display(first_line, avail)
            desc_part = f" · {clipped_desc}" if clipped_desc else ""
            lines.append(f"{left}{desc_part}")

    lines.append("\n  · Quick Run: /<skill-name> [prompt]  |  Management: /skill enable|disable <id>")
    app._append(DisplayKind.METADATA, "\n".join(lines))
    return True
