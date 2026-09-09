from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable


_MAX_SKILL_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class SkillManifest:
    identifier: str
    description: str
    source: str
    digest: str
    instruction: str
    trusted: bool
    sources: tuple[str, ...] = ()


class SkillRegistry:
    def __init__(self, skills: Iterable[SkillManifest] = (), errors: Iterable[str] = ()) -> None:
        self._skills = {item.identifier: item for item in skills}
        self._errors = tuple(errors)

    @classmethod
    def discover(cls, workspace: Path, user_root: Path | None = None) -> SkillRegistry:
        roots = ((user_root or Path.home() / ".agents" / "skills", True), (Path(workspace) / ".agents" / "skills", False))
        grouped: dict[str, list[SkillManifest]] = {}; errors: list[str] = []
        for root, trusted in roots:
            if not root.is_dir() or root.is_symlink():
                continue
            for path in sorted(root.iterdir()):
                if not path.is_dir() or path.is_symlink():
                    continue
                try:
                    skill = _read_skill(path, trusted)
                except (OSError, UnicodeError, ValueError) as error:
                    errors.append(f"{path.name}: {type(error).__name__}")
                    continue
                grouped.setdefault(skill.identifier, []).append(skill)
        active: list[SkillManifest] = []
        for identifier, candidates in grouped.items():
            digests = {item.digest for item in candidates}
            if len(digests) != 1:
                errors.append(f"{identifier}: conflicting digests")
                continue
            first = candidates[0]
            active.append(replace(first, sources=tuple(item.source for item in candidates)))
        return cls(active, errors)

    def list(self) -> tuple[SkillManifest, ...]: return tuple(sorted(self._skills.values(), key=lambda item: item.identifier))
    def get(self, identifier: str) -> SkillManifest: return self._skills[identifier]
    def errors(self) -> tuple[str, ...]: return self._errors


class SkillActivation:
    def __init__(self, registry: SkillRegistry, *, max_chars: int = 64_000) -> None:
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
        from code_agent.context.measurements import with_system_prompt
        return with_system_prompt(bundle, bundle.system_prompt + ("\n\n" + content if content else ""))


def _read_skill(directory: Path, trusted: bool) -> SkillManifest:
    instruction = directory / "SKILL.md"
    if not instruction.is_file() or instruction.is_symlink() or instruction.stat().st_size > _MAX_SKILL_BYTES:
        raise ValueError("invalid skill directory")
    content = instruction.read_text("utf-8")
    identifier, description = _frontmatter(content)
    identifier = identifier or directory.name
    if not _IDENTIFIER.fullmatch(identifier) or not content.strip():
        raise ValueError("invalid skill identifier")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return SkillManifest(identifier, description or identifier, str(directory), digest, content, trusted)


def _frontmatter(content: str) -> tuple[str | None, str | None]:
    if not content.startswith("---\n"):
        return None, None
    closing = content.find("\n---", 4)
    if closing < 0:
        raise ValueError("invalid frontmatter")
    values: dict[str, str] = {}
    current_key: str | None = None
    multiline: list[str] = []
    for line in content[4:closing].splitlines():
        stripped = line.strip()
        if (line.startswith(("  ", "\t")) or (current_key and not line.partition(":")[1])) and current_key:
            if stripped:
                multiline.append(stripped)
            continue
        if current_key and multiline:
            values[current_key] = " ".join(multiline)
            current_key = None
            multiline = []
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"name", "description"}:
            k = key.strip()
            v = value.strip().strip('"').strip("'")
            if v in {">", ">-", "|", "|-", ""}:
                current_key = k
                multiline = []
            else:
                values[k] = v
                current_key = None
    if current_key and multiline:
        values[current_key] = " ".join(multiline)
    return values.get("name"), values.get("description")
