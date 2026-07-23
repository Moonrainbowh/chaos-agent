from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from code_agent.orchestration.modes import ModeRegistry
from code_agent.orchestration.models import ReasoningEffort

from .models import PluginManifest, PluginRisk
from .status import PluginHostStatus, PluginReloadState, PluginStatus


_RISK_ORDER = {
    PluginRisk.READ: 0,
    PluginRisk.WRITE: 1,
    PluginRisk.NETWORK: 2,
    PluginRisk.CRITICAL: 3,
}
_EFFORT_ORDER = {
    ReasoningEffort.MINIMAL: 0,
    ReasoningEffort.LOW: 1,
    ReasoningEffort.MEDIUM: 2,
    ReasoningEffort.HIGH: 3,
    ReasoningEffort.XHIGH: 4,
}


@dataclass(frozen=True)
class RegisteredContribution:
    plugin_id: str
    namespace: str
    kind: str
    identifier: str
    value: object

    @property
    def qualified_id(self) -> str:
        return f"{self.namespace}.{self.identifier}"


@dataclass(frozen=True)
class ContributionSnapshot:
    manifests: tuple[PluginManifest, ...] = ()
    contributions: tuple[RegisteredContribution, ...] = ()
    errors: tuple[str, ...] = ()

    def active(self, revoked: frozenset[str] = frozenset()) -> tuple[RegisteredContribution, ...]:
        return tuple(item for item in self.contributions if item.plugin_id not in revoked)


class PluginRegistryBuilder:
    def __init__(
        self,
        mode_registry: ModeRegistry,
        *,
        host_actions: Iterable[str] = (),
        host_action_risks: dict[str, PluginRisk] | None = None,
        mcp_tools: Iterable[str] = (),
        controllers: Iterable[str] = (),
        builtin_namespaces: Iterable[str] = ("host", "core", "mcp"),
        builtin_ids: Iterable[str] = (),
    ) -> None:
        if not isinstance(mode_registry, ModeRegistry):
            raise TypeError("mode_registry must be ModeRegistry")
        self._modes = mode_registry
        self._actions = frozenset(host_actions)
        self._risks = dict(host_action_risks or {})
        self._mcp = frozenset(mcp_tools)
        self._controllers = frozenset(controllers)
        self._builtin_namespaces = frozenset(builtin_namespaces)
        self._builtin_ids = frozenset(builtin_ids)

    def build(self, manifests: Iterable[PluginManifest]) -> ContributionSnapshot:
        accepted: list[PluginManifest] = []
        registered: list[RegisteredContribution] = []
        errors: list[str] = []
        namespaces = set(self._builtin_namespaces)
        identifiers = set(self._builtin_ids)
        seen_plugins: set[str] = set()
        for manifest in manifests:
            if not isinstance(manifest, PluginManifest):
                raise TypeError("manifests must contain PluginManifest values")
            if manifest.identifier in seen_plugins:
                errors.append(f"{manifest.identifier}: duplicate plugin id")
                continue
            seen_plugins.add(manifest.identifier)
            if manifest.namespace in namespaces:
                errors.append(f"{manifest.identifier}: namespace conflict")
                continue
            try:
                pending = self._validate_manifest(manifest, identifiers)
            except ValueError as error:
                errors.append(f"{manifest.identifier}: {error}")
                continue
            namespaces.add(manifest.namespace)
            identifiers.update(item.qualified_id for item in pending)
            accepted.append(manifest)
            registered.extend(pending)
        return ContributionSnapshot(tuple(accepted), tuple(registered), tuple(errors))

    def _validate_manifest(
        self, manifest: PluginManifest, existing: set[str]
    ) -> tuple[RegisteredContribution, ...]:
        result: list[RegisteredContribution] = []
        groups = (
            ("tool", manifest.contributions.tools),
            ("command", manifest.contributions.commands),
            ("mode", manifest.contributions.modes),
            ("agent", manifest.contributions.agents),
            ("event", manifest.contributions.events),
        )
        local_ids: set[str] = set()
        for kind, values in groups:
            for value in values:
                item = RegisteredContribution(
                    manifest.identifier, manifest.namespace, kind, value.identifier, value
                )
                if item.qualified_id in existing or item.qualified_id in local_ids:
                    raise ValueError(f"contribution conflict: {item.qualified_id}")
                local_ids.add(item.qualified_id)
                self._validate_value(kind, value)
                result.append(item)
        return tuple(result)

    def _validate_value(self, kind: str, value: object) -> None:
        if kind == "tool":
            target = value.target  # type: ignore[attr-defined]
            if target not in self._actions and target not in self._mcp:
                raise ValueError(f"unknown tool target: {target}")
            host_risk = self._risks.get(target)
            if host_risk is not None and _RISK_ORDER[value.risk] < _RISK_ORDER[host_risk]:  # type: ignore[attr-defined]
                raise ValueError("plugin tool risk cannot lower host risk")
        elif kind == "command":
            if value.controller not in self._controllers:  # type: ignore[attr-defined]
                raise ValueError("unknown command controller")
        elif kind in {"mode", "agent"}:
            base = self._modes.definition(value.base_mode)  # type: ignore[attr-defined]
            if not set(value.tool_names).issubset(base.tool_names):  # type: ignore[attr-defined]
                raise ValueError(f"{kind} tools must narrow the base mode")
            effort = getattr(value, "reasoning_effort", None)
            if effort is not None and _EFFORT_ORDER[effort] > _EFFORT_ORDER[base.reasoning_effort]:
                raise ValueError("plugin mode cannot increase reasoning effort")
        elif kind == "event":
            action = value.action  # type: ignore[attr-defined]
            if action is not None and action.target not in self._actions and action.target not in self._mcp:
                raise ValueError("event proposal has an unknown action target")
            host_risk = None if action is None else self._risks.get(action.target)
            if (
                action is not None
                and host_risk is not None
                and _RISK_ORDER[action.risk] < _RISK_ORDER[host_risk]
            ):
                raise ValueError("event risk cannot lower host risk")


class PluginHost:
    """Stage snapshots at task boundaries while making revocation immediate."""

    def __init__(self, snapshot: ContributionSnapshot = ContributionSnapshot()) -> None:
        if not isinstance(snapshot, ContributionSnapshot):
            raise TypeError("snapshot must be ContributionSnapshot")
        self._current = snapshot
        self._staged: ContributionSnapshot | None = None
        self._explicitly_disabled: set[str] = set()
        self._revoked: set[str] = _disabled_plugins(snapshot)
        self._generation = 0

    def stage(self, snapshot: ContributionSnapshot) -> None:
        if not isinstance(snapshot, ContributionSnapshot):
            raise TypeError("snapshot must be ContributionSnapshot")
        self._staged = snapshot

    def apply(self, *, task_active: bool) -> bool:
        if task_active or self._staged is None:
            return False
        self._current = self._staged
        self._staged = None
        self._revoked = self._explicitly_disabled | _disabled_plugins(self._current)
        self._generation += 1
        return True

    def list(self) -> tuple[PluginStatus, ...]:
        return _plugin_statuses(self._current, self._revoked)

    def status(self, plugin_id: str | None = None) -> tuple[PluginStatus, ...]:
        values = self.list()
        if plugin_id is None:
            return values
        _require_plugin_id(plugin_id)
        selected = tuple(item for item in values if item.plugin_id == plugin_id)
        if not selected:
            raise KeyError("plugin is not loaded")
        return selected

    def state(self) -> PluginHostStatus:
        staged = (
            ()
            if self._staged is None
            else _plugin_statuses(
                self._staged, self._explicitly_disabled | _disabled_plugins(self._staged)
            )
        )
        return PluginHostStatus(
            self._generation, self.reload_state, self.list(), staged
        )

    @property
    def reload_state(self) -> PluginReloadState:
        return (
            PluginReloadState.IDLE
            if self._staged is None
            else PluginReloadState.STAGED
        )

    def disable(self, plugin_id: str) -> PluginStatus:
        manifest = self._loaded_manifest(plugin_id)
        self._explicitly_disabled.add(plugin_id)
        self._revoked.add(plugin_id)
        self._generation += 1
        return _plugin_status(manifest, enabled=False)

    def revoke(self, plugin_id: str) -> bool:
        try:
            self.disable(plugin_id)
        except KeyError:
            return False
        return True

    def enable(self, plugin_id: str) -> PluginStatus:
        manifest = self._loaded_manifest(plugin_id)
        if not manifest.trusted:
            raise PermissionError("only a loaded trusted plugin can be enabled")
        self._explicitly_disabled.discard(plugin_id)
        self._revoked.discard(plugin_id)
        self._generation += 1
        return _plugin_status(manifest, enabled=True)

    def contributions(self, kind: str | None = None) -> tuple[RegisteredContribution, ...]:
        values = self._current.active(frozenset(self._revoked))
        return values if kind is None else tuple(item for item in values if item.kind == kind)

    @property
    def snapshot(self) -> ContributionSnapshot:
        return self._current

    @property
    def generation(self) -> int:
        return self._generation

    def manifest_digest(self, plugin_id: str) -> str:
        return self._loaded_manifest(plugin_id).digest

    def is_active(self, plugin_id: str, digest: str, generation: int) -> bool:
        if generation != self._generation or plugin_id in self._revoked:
            return False
        return any(
            manifest.identifier == plugin_id and manifest.digest == digest
            for manifest in self._current.manifests
        )

    def _loaded_manifest(self, plugin_id: str) -> PluginManifest:
        _require_plugin_id(plugin_id)
        for manifest in self._current.manifests:
            if manifest.identifier == plugin_id:
                return manifest
        raise KeyError("plugin is not loaded")


def _plugin_statuses(
    snapshot: ContributionSnapshot, revoked: set[str]
) -> tuple[PluginStatus, ...]:
    return tuple(
        _plugin_status(manifest, enabled=manifest.identifier not in revoked)
        for manifest in sorted(snapshot.manifests, key=lambda item: item.identifier)
    )


def _disabled_plugins(snapshot: ContributionSnapshot) -> set[str]:
    return {item.identifier for item in snapshot.manifests if not item.enabled}


def _plugin_status(manifest: PluginManifest, *, enabled: bool) -> PluginStatus:
    return PluginStatus(
        manifest.identifier,
        manifest.namespace,
        manifest.version,
        manifest.digest,
        manifest.source,
        manifest.trusted,
        enabled,
    )


def _require_plugin_id(plugin_id: str) -> None:
    if not isinstance(plugin_id, str):
        raise TypeError("plugin_id must be text")
    if not plugin_id.strip():
        raise ValueError("plugin_id must not be blank")
