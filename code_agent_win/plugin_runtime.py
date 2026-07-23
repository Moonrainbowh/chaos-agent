from __future__ import annotations

import json
import os
from collections.abc import Callable
from inspect import isawaitable
from pathlib import Path

from code_agent.core.models import ActionRequest, ToolDefinition
from code_agent.orchestration.modes import ModeRegistry
from code_agent.plugins.manifest import ManifestError, PluginTrustStore, load_manifest
from code_agent.plugins.models import PluginRisk
from code_agent.plugins.registry import PluginHost, PluginRegistryBuilder
from code_agent.plugins.commands import PluginCommandCatalog
from code_agent.core.cancellation import CancellationToken
from code_agent.interfaces.approval import ApprovalRequest
from code_agent.policy.models import DecisionOutcome
from code_agent.plugins.events import (
    DeclarativeEventRouter,
    EventProjection,
    PluginProposal,
)
from code_agent.plugins.runtime import PluginEventRuntime


class PluginToolBridge:
    def __init__(self, host: PluginHost) -> None:
        self._host = host

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                item.qualified_id,
                item.value.description,
                item.value.input_schema,
            )
            for item in self._host.contributions("tool")
        )

    def targets(self) -> dict[str, str]:
        return {item.qualified_id: item.value.target for item in self._host.contributions("tool")}

    def risk_map(self) -> dict[str, str]:
        return {item.qualified_id: item.value.risk.value for item in self._host.contributions("tool")}


class PluginCommandController:
    """Map declarative plugin commands to bounded Host-owned controllers."""
    def __init__(
        self,
        host: PluginHost,
        *,
        discover: Callable[[], tuple[object, object]] | None = None,
        on_change: Callable[[], object] | None = None,
    ) -> None:
        self._host = host
        self._catalog = PluginCommandCatalog(host)
        self._app: object | None = None
        self._discover, self._on_change = discover, on_change
        self._errors: tuple[str, ...] = ()

    def attach(self, app: object) -> None:
        self._app = app

    @property
    def errors(self) -> tuple[str, ...]: return self._errors

    def list(self) -> object: return self._host.list()
    def status(self, plugin_id: str | None = None) -> object: return self._host.status(plugin_id)

    async def enable(self, plugin_id: str) -> object:
        if self._task_active(): raise RuntimeError("plugin enable is available only when idle")
        result = self._host.enable(plugin_id)
        await self._notify_change()
        return result

    async def disable(self, plugin_id: str) -> object:
        if self._task_active(): raise RuntimeError("plugin disable is available only when idle")
        result = self._host.disable(plugin_id)
        await self._notify_change()
        return result

    async def reload(self) -> object:
        if self._discover is None:
            raise RuntimeError("plugin discovery is unavailable")
        try:
            snapshot, errors = self._discover()
            self._host.stage(snapshot)
            applied = self._host.apply(task_active=self._task_active())
            self._errors = _bounded_errors(errors)
        except Exception as error:
            self._errors = (f"discover:{type(error).__name__}",)
            raise RuntimeError("plugin reload failed") from None
        if applied: await self._notify_change()
        return "Plugin reload applied" if applied else "Plugin reload staged"

    async def apply_staged(self, *, idle: bool = False) -> bool:
        applied = self._host.apply(task_active=not idle and self._task_active())
        if applied: await self._notify_change()
        return applied

    def _task_active(self) -> bool:
        task = None if self._app is None else getattr(self._app, "_run_task", None)
        return task is not None and not task.done()

    async def _notify_change(self) -> None:
        if self._on_change is None:
            return
        try:
            result = self._on_change()
            if isawaitable(result): await result
        except Exception as error:
            self._errors = (*self._errors, f"on_change:{type(error).__name__}")[-32:]
            raise RuntimeError("plugin control refresh failed") from None

    async def execute_command(
        self, qualified_id: str, arguments: tuple[str, ...]
    ) -> object:
        invocation = self._catalog.resolve(qualified_id, arguments)
        if not self._host.is_active(
            invocation.plugin_id,
            invocation.digest,
            invocation.generation,
        ):
            raise RuntimeError("plugin command is stale")
        if self._app is None:
            raise RuntimeError("plugin command controller is unavailable")
        if invocation.controller == "session":
            if len(arguments) != 1:
                raise ValueError("session command requires one thread id")
            return await self._app.restore_thread(arguments[0])
        if invocation.controller == "workflow":
            return await self._app.submit(
                "/流程" + (" " + " ".join(arguments) if arguments else "")
            )
        prompt = " ".join(arguments).strip()
        if not prompt:
            raise ValueError("plugin command requires an instruction")
        if invocation.controller == "review":
            prompt = "Review the current task and report findings: " + prompt
        if invocation.controller in {"review", "task"}:
            return await self._app.submit(prompt)
        raise RuntimeError("plugin command controller is unavailable")


class PluginProposalActionExecutor:
    """Apply plugin proposal risk, then delegate the target to Host policy again."""

    def __init__(self, dispatcher: object) -> None:
        self._dispatcher = dispatcher

    async def execute(
        self, proposal: PluginProposal, cancellation: CancellationToken
    ) -> object:
        action = proposal.action
        if action is None:
            raise ValueError("plugin proposal has no action")
        source = ActionRequest(
            f"plugin-event:{proposal.plugin_id}:{proposal.subscription_id}",
            _event_policy_name(proposal.plugin_id, proposal.subscription_id),
            dict(action.arguments),
        )
        decision = self._dispatcher.policy.evaluate(source)
        if decision.outcome is DecisionOutcome.DENY:
            raise PermissionError("plugin proposal denied")
        if decision.outcome is DecisionOutcome.ASK:
            if not self._dispatcher.interactive:
                raise PermissionError("plugin proposal requires TUI approval")
            approved = await self._dispatcher.approvals.request(
                ApprovalRequest(
                    source.id,
                    source.name,
                    dict(source.arguments),
                    decision.risk.value,
                    action.target,
                    decision.reason,
                ),
                cancellation,
            )
            if not approved:
                raise PermissionError("plugin proposal rejected")
        target = ActionRequest(source.id, action.target, dict(action.arguments))
        return await self._dispatcher.dispatch(target, cancellation)


class PluginEventCoordinator:
    def __init__(
        self,
        host: PluginHost,
        interactions: object,
        dispatcher: object,
    ) -> None:
        self._runtime = PluginEventRuntime(
            host,
            DeclarativeEventRouter(host),
            interactions,
            PluginProposalActionExecutor(dispatcher),
        )

    async def observe(
        self,
        event_kind: str,
        task_id: str,
        fields: dict[str, object],
        cancellation: CancellationToken,
    ) -> object:
        return await self._runtime.handle(
            EventProjection(event_kind, task_id, fields), cancellation
        )


def plugin_event_risks(host: PluginHost) -> dict[str, str]:
    result = {}
    for registered in host.contributions("event"):
        action = getattr(registered.value, "action", None)
        if action is not None:
            result[
                _event_policy_name(
                    registered.plugin_id, registered.identifier
                )
            ] = action.risk.value
    return result


def _event_policy_name(plugin_id: str, subscription_id: str) -> str:
    return f"plugin_event.{plugin_id}.{subscription_id}"


def load_plugins(
    workspace_root: Path,
    modes: ModeRegistry,
    *,
    host_actions: tuple[str, ...],
    host_risks: dict[str, PluginRisk],
    mcp_tools: tuple[str, ...] = (),
    controllers: tuple[str, ...] = (),
) -> tuple[PluginHost, tuple[str, ...]]:
    trust = PluginTrustStore(_read_trust_store())
    manifests = []
    errors: list[str] = []
    for path in _manifest_paths(workspace_root):
        try:
            manifest = load_manifest(path, trust, host_api="1")
            manifests.append(manifest)
            if not manifest.enabled and not manifest.trusted:
                errors.append(f"{manifest.identifier}: inactive (untrusted)")
        except (OSError, ManifestError) as error:
            errors.append(f"{path}: {type(error).__name__}")
    builder = PluginRegistryBuilder(
        modes,
        host_actions=host_actions,
        host_action_risks=host_risks,
        mcp_tools=mcp_tools,
        controllers=controllers,
    )
    snapshot = builder.build(manifests)
    return PluginHost(snapshot), tuple(errors) + snapshot.errors


def _manifest_paths(workspace_root: Path) -> tuple[Path, ...]:
    local = _local_data_root() / "plugins"
    workspace = workspace_root / ".chaos-agent" / "plugins"
    paths: list[Path] = []
    for root in (local, workspace):
        if not root.is_dir() or root.is_symlink():
            continue
        paths.extend(
            child / "plugin.json"
            for child in sorted(root.iterdir())
            if child.is_dir() and not child.is_symlink() and (child / "plugin.json").is_file()
        )
    return tuple(paths)


def _read_trust_store() -> dict[str, str]:
    path = _local_data_root() / "plugin-trust.json"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024:
        return {}
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _local_data_root() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "chaos-agent"


def _bounded_errors(values: object) -> tuple[str, ...]:
    values = (values,) if isinstance(values, (str, bytes)) else values
    return tuple(str(value)[:256] for value in tuple(values)[:32])
