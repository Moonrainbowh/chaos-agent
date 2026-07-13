from __future__ import annotations

import hashlib
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try: import tomllib
except ModuleNotFoundError: import tomli as tomllib


@dataclass(frozen=True)
class SkillManifest:
    identifier: str
    description: str
    source: str
    digest: str
    instruction: str
    trusted: bool


class SkillRegistry:
    def __init__(self, skills: Iterable[SkillManifest] = ()) -> None:
        supplied = tuple(skills)
        self._skills = {skill.identifier: skill for skill in supplied}
        if len(self._skills) != len(supplied): raise ValueError("duplicate skill identifiers")

    @classmethod
    def discover(cls, workspace: Path, user_root: Path | None = None) -> SkillRegistry:
        roots = ((user_root or Path.home() / ".chaos-agent" / "skills", True), (workspace / ".chaos-agent" / "skills", False))
        found: list[SkillManifest] = []
        for root, trusted in roots:
            if root.is_dir(): found.extend(_read_skill(path, trusted) for path in root.iterdir() if path.is_dir())
        return cls(found)

    def list(self) -> tuple[SkillManifest, ...]: return tuple(self._skills.values())
    def get(self, identifier: str) -> SkillManifest: return self._skills[identifier]


class SkillActivation:
    def __init__(self, registry: SkillRegistry, *, max_chars: int = 12_000) -> None:
        self._registry, self._max_chars, self._active = registry, max_chars, []

    def activate(self, identifier: str, *, approved: bool = False) -> SkillManifest:
        skill = self._registry.get(identifier)
        if not skill.trusted and not approved: raise PermissionError("workspace skill requires explicit activation")
        if identifier not in self._active: self._active.append(identifier)
        if len(self.render()) > self._max_chars: self._active.pop(); raise ValueError("skill context budget exceeded")
        return skill

    def deactivate(self, identifier: str) -> None:
        if identifier in self._active: self._active.remove(identifier)

    def available(self) -> tuple[SkillManifest, ...]: return self._registry.list()
    def info(self, identifier: str) -> SkillManifest: return self._registry.get(identifier)
    def active(self) -> tuple[SkillManifest, ...]: return tuple(self._registry.get(identifier) for identifier in self._active)
    def render(self) -> str: return "\n\n".join(f"[Skill {skill.identifier} {skill.digest}]\n{skill.instruction}" for skill in self.active())


class SkillContextBuilder:
    """Append the active, already bounded Skill instructions to each context build."""
    def __init__(self, inner: object, activation: SkillActivation) -> None: self._inner, self._activation = inner, activation
    async def build(self, *args: object, **kwargs: object) -> object:
        bundle = await self._inner.build(*args, **kwargs)
        content = self._activation.render()
        return replace(bundle, system_prompt=bundle.system_prompt + ("\n\n" + content if content else ""))


def _read_skill(directory: Path, trusted: bool) -> SkillManifest:
    manifest, instruction = directory / "skill.toml", directory / "SKILL.md"
    if not manifest.is_file() or not instruction.is_file(): raise ValueError(f"invalid skill directory: {directory.name}")
    raw, content = tomllib.loads(manifest.read_text("utf-8")), instruction.read_text("utf-8")
    data = raw.get("skill") if isinstance(raw, dict) else None
    if not isinstance(data, dict): raise ValueError("skill manifest requires [skill]")
    identifier, description = data.get("id"), data.get("description")
    if not isinstance(identifier, str) or not identifier.replace("-", "").replace("_", "").isalnum() or not isinstance(description, str) or not content or len(content) > 8_000: raise ValueError("invalid skill manifest")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return SkillManifest(identifier, description, str(directory), digest, content, trusted)
