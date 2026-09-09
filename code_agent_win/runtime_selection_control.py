from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from code_agent.orchestration.models import (
    AgentTopology,
    ModeSnapshot,
    RuntimeReasoningEffort,
    RuntimeSelection,
)
from code_agent.orchestration.modes import (
    attach_runtime_selection,
    freeze_runtime_selection,
)
from code_agent.providers.config import ApiProtocol, ModelProfile


@dataclass(frozen=True)
class RuntimeSelectionSummary:
    topology: str
    profile: str
    model: str
    protocol: str
    reasoning_effort: str
    max_output_tokens: int
    legacy_mode: str


class RuntimeSelectionControl:
    """Atomically rebuild an idle runtime from independent frozen choices."""

    def __init__(
        self,
        profiles: Mapping[str, ModelProfile],
        current: ModeSnapshot,
        apply: Callable[[ModeSnapshot], Awaitable[None]],
        current_supplier: Callable[[], ModeSnapshot] | None = None,
    ) -> None:
        if current.runtime_selection is None:
            raise ValueError("current mode snapshot has no runtime selection")
        if not callable(apply):
            raise TypeError("apply must be callable")
        self._profiles = dict(profiles)
        self._snapshot = current
        self._apply = apply
        self._current_supplier = current_supplier
        self._lock = asyncio.Lock()

    @property
    def current(self) -> RuntimeSelectionSummary:
        return _selection_summary(self._selection)

    @property
    def snapshot(self) -> ModeSnapshot:
        if self._current_supplier is not None:
            return self._current_supplier()
        return self._snapshot

    def list_profiles(self) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (profile.name, profile.provider.model, profile.provider.api.value)
            for profile in self._profiles.values()
        )

    def profiles(self) -> tuple[tuple[str, str, str], ...]:
        """Compatibility entrypoint consumed by the slash-command UI."""

        return self.list_profiles()

    def register_profile(self, profile: ModelProfile) -> None:
        """Expose an in-memory profile to subsequent explicit selections."""
        if not isinstance(profile, ModelProfile):
            raise TypeError("profile must be a ModelProfile")
        existing = self._profiles.get(profile.name)
        if existing is not None and existing != profile:
            raise ValueError("profile name already belongs to another configuration")
        self._profiles[profile.name] = profile

    def list_topologies(self) -> tuple[str, ...]:
        return tuple(item.value for item in AgentTopology)

    def list_reasoning_efforts(self) -> tuple[str, ...]:
        return tuple(item.value for item in RuntimeReasoningEffort)

    async def use(
        self,
        *,
        topology: AgentTopology | str | None = None,
        profile: str | None = None,
        reasoning_effort: RuntimeReasoningEffort | str | None = None,
        idle: bool,
    ) -> RuntimeSelectionSummary:
        if not idle:
            raise RuntimeError("runtime switching is available only when idle")
        async with self._lock:
            current = self._selection
            selection = freeze_runtime_selection(
                self._profiles,
                profile_id=current.profile_id if profile is None else profile,
                topology=current.topology if topology is None else topology,
                reasoning_effort=(
                    current.reasoning_effort
                    if reasoning_effort is None
                    else reasoning_effort
                ),
                legacy_mode=self.snapshot.definition.mode,
            )
            validate_profile_reasoning(
                self._profiles[selection.profile_id], selection.reasoning_effort
            )
            candidate = attach_runtime_selection(self.snapshot, selection)
            await self._apply(candidate)
            if self._current_supplier is None:
                self._snapshot = candidate
            return self.current

    def observe(self, snapshot: ModeSnapshot) -> None:
        if snapshot.runtime_selection is None:
            raise ValueError("observed mode snapshot has no runtime selection")
        if self._current_supplier is None:
            self._snapshot = snapshot

    @property
    def _selection(self) -> RuntimeSelection:
        selection = self.snapshot.runtime_selection
        if selection is None:
            raise RuntimeError("runtime selection is unavailable")
        return selection


def _selection_summary(selection: RuntimeSelection) -> RuntimeSelectionSummary:
    return RuntimeSelectionSummary(
        selection.topology.value,
        selection.profile_id,
        selection.model,
        selection.api_protocol,
        selection.reasoning_effort.value,
        selection.max_output_tokens,
        selection.legacy_mode.value,
    )


def validate_profile_reasoning(
    profile: ModelProfile, effort: RuntimeReasoningEffort | str
) -> None:
    selected = RuntimeReasoningEffort(effort)
    if (
        profile.provider.api is ApiProtocol.ANTHROPIC_MESSAGES
        and selected is not RuntimeReasoningEffort.MEDIUM
    ):
        raise ValueError(
            "anthropic_messages has no structured reasoning-effort mapping; "
            "only the default medium prompt policy is available"
        )
