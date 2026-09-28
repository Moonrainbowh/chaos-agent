from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SkillRequest:
    """A user-declared Skill selection before execution or composition."""

    skill_ids: tuple[str, ...]
    prompt: str


def resolve_skill_request(text: str, skills: object) -> SkillRequest | None:
    """Parse explicit Skill syntax without matching ordinary natural language."""
    if not isinstance(text, str) or not text.startswith(("/", ":")):
        return None
    body = text[1:].strip()
    if not body:
        return None
    parts = body.split(None, 1)
    head = parts[0]
    tail = parts[1].strip() if len(parts) == 2 else ""
    if head.casefold() in {"skill", "skills", "技能"}:
        if not tail:
            return None
        parts = tail.split(None, 1)
        head = parts[0]
        tail = parts[1].strip() if len(parts) == 2 else ""
        if head.casefold() in {
            "list", "info", "enable", "disable", "source", "reload",
            "run", "use", "active", "explain", "sources", "列表", "信息", "启用", "禁用", "来源", "重载", "运行", "活动", "说明", "解释", "当前",
        }:
            return None
    skill_ids_list = [item.strip() for item in head.split("+") if item.strip()]
    position = 0
    while position < len(tail):
        match = re.match(r"\s*\+\s*([^\s+]+)", tail[position:])
        if match is None:
            break
        skill_ids_list.append(match.group(1))
        position += match.end()
    prompt = tail[position:].strip()
    skill_ids = tuple(skill_ids_list)
    if not skill_ids or len(set(skill_ids)) != len(skill_ids):
        return None
    try:
        for skill_id in skill_ids:
            skills.info(skill_id)
    except (KeyError, LookupError, ValueError):
        return None
    return SkillRequest(skill_ids, prompt)
