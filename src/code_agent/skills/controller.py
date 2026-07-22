from __future__ import annotations

from pathlib import Path
from typing import Protocol

from code_agent.sessions.models import SkillActivationRecord

from .registry import SkillActivation, SkillManifest, SkillRegistry


class SkillApproval(Protocol):
    async def approve(
        self, identifier: str, source: str, digest: str
    ) -> bool: ...


class SkillStore(Protocol):
    async def save_skill_activation(
        self, thread_id: str, skill_id: str, source: str, digest: str
    ) -> SkillActivationRecord: ...

    async def list_skill_activations(
        self, thread_id: str
    ) -> tuple[SkillActivationRecord, ...]: ...

    async def remove_skill_activation(
        self, thread_id: str, skill_id: str
    ) -> bool: ...


class SkillController:
    """Own thread-scoped activation while re-reading trusted Skill text."""

    def __init__(
        self,
        workspace: Path,
        store: SkillStore,
        approval: SkillApproval,
        *,
        user_root: Path | None = None,
        max_chars: int = 12_000,
    ) -> None:
        self._workspace = Path(workspace)
        self._user_root = user_root
        self._store = store
        self._approval = approval
        self._max_chars = max_chars
        self._registry = SkillRegistry.discover(self._workspace, user_root)
        self._activations: dict[str, SkillActivation] = {}

    def list(self) -> tuple[SkillManifest, ...]:
        return self._registry.list()

    def info(self, identifier: str) -> SkillManifest:
        return self._registry.get(identifier)

    def sources(self, identifier: str) -> tuple[str, ...]:
        skill = self.info(identifier)
        return skill.sources or (skill.source,)

    def errors(self) -> tuple[str, ...]:
        return self._registry.errors()

    def activation(self, thread_id: str) -> SkillActivation:
        return self._activations.setdefault(
            thread_id,
            SkillActivation(self._registry, max_chars=self._max_chars),
        )

    async def enable(
        self, thread_id: str, identifier: str
    ) -> SkillManifest:
        skill = self.info(identifier)
        approved = skill.trusted
        if not approved:
            approved = await self._approval.approve(
                skill.identifier, skill.source, skill.digest
            )
        if not approved:
            raise PermissionError("workspace skill activation was rejected")
        activated = self.activation(thread_id).activate(
            identifier, approved=True
        )
        await self._store.save_skill_activation(
            thread_id, identifier, activated.source, activated.digest
        )
        return activated

    async def disable(self, thread_id: str, identifier: str) -> bool:
        self.activation(thread_id).deactivate(identifier)
        return await self._store.remove_skill_activation(thread_id, identifier)

    async def restore(
        self, thread_id: str
    ) -> tuple[SkillManifest, ...]:
        activation = SkillActivation(
            self._registry, max_chars=self._max_chars
        )
        for record in await self._store.list_skill_activations(thread_id):
            try:
                skill = self._registry.get(record.skill_id)
            except KeyError:
                await self._store.remove_skill_activation(
                    thread_id, record.skill_id
                )
                continue
            sources = skill.sources or (skill.source,)
            if record.digest != skill.digest or record.source not in sources:
                await self._store.remove_skill_activation(
                    thread_id, record.skill_id
                )
                continue
            activation.activate(record.skill_id, approved=True)
        self._activations[thread_id] = activation
        return activation.active()

    async def reload(
        self, thread_id: str | None = None
    ) -> tuple[SkillManifest, ...]:
        self._registry = SkillRegistry.discover(
            self._workspace, self._user_root
        )
        self._activations.clear()
        if thread_id is not None:
            await self.restore(thread_id)
        return self.list()
