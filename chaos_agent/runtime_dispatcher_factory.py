from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path

from code_agent.capabilities import CapabilityStrategy
from code_agent.core.engine import AgentEngine
from code_agent.core.action_execution import ActionLineage, ActionExecutionContext
from code_agent.core.task import TaskAuthorization
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentMode,
    AgentTopology,
    ModeSnapshot,
)
from code_agent.providers.config import ModelProfile
from chaos_agent.agent_modes import main_tools_for_mode
from chaos_agent.application_context import engine_for
from chaos_agent.host_composition import compose_subagents
from chaos_agent.runtime_client_cleanup import schedule_partial_client_close
from chaos_agent.subagents import RestrictedDispatcher, SubagentRuntime


class RuntimeDispatcherFactory:
    def __init__(
        self,
        *,
        root: Path,
        profiles: Mapping[str, ModelProfile],
        client_factory: Callable[..., object],
        context_for: Callable[[ModeSnapshot, object, ModelProfile], object],
        dispatcher: object,
        sessions: object,
        plugin_bridge: object,
        plugin_bindings: object,
        capability_strategy: CapabilityStrategy = CapabilityStrategy.HYBRID,
    ) -> None:
        self._root, self._profiles = root, profiles
        self._client_factory, self._context_for = client_factory, context_for
        self._dispatcher, self._sessions = dispatcher, sessions
        self._plugin_bridge, self._plugin_bindings = plugin_bridge, plugin_bindings
        self.capability_strategy = capability_strategy
        self._mcp_names: tuple[str, ...] = ()
        self._base_definitions: dict[AgentMode, object] = {}
        self.plugin_mode_digests: set[str] = set()
        self._partial_closures: set[asyncio.Task[None]] = set()

    def child_engine(self, agent: AgentDefinition, parent=None, authorization=None) -> tuple[AgentEngine, object]:
        if (not isinstance(parent, ActionExecutionContext) or not parent.task_id
                or not isinstance(authorization, TaskAuthorization)):
            raise ValueError("child engine requires concrete typed parent execution authority")
        sessions = self._sessions.for_owner(parent.owner_thread_id)
        root = Path(authorization.workspace_root).resolve()
        profile = self._profiles[agent.mode.profile_id]
        client = self._client_factory(
            profile.provider,
            reasoning_effort=agent.mode.effective_reasoning_effort,
        )
        try:
            engine = engine_for(
                client,
                profile,
                self._context_for.for_child(agent.mode, client, profile, root, sessions),
                RestrictedDispatcher(self._dispatcher, agent.effective_tools, compact_tools=True,
                                     frozen_authorization=authorization),
                sessions,
                root,
                agent.mode,
                capability_strategy=self.capability_strategy,
                action_lineage=ActionLineage(parent.owner_thread_id, parent.task_id, parent.request_id),
                inherited_authorization=authorization,
            )
            return engine, client
        except BaseException:
            self.retire_partial_client(client)
            raise

    def retire_partial_client(self, client):
        """Close a client allocated by a synchronous partial composition."""
        schedule_partial_client_close(client, self._partial_closures)

    def attach_subagents(
        self,
        *,
        thread_binding: object,
        modes: object,
        plugin_host: object,
        mode_snapshots: Mapping[AgentMode, ModeSnapshot],
    ) -> SubagentRuntime:
        self._base_definitions = {
            mode: snapshot.definition for mode, snapshot in mode_snapshots.items()
        }
        subagents, self._mcp_names, self.plugin_mode_digests = compose_subagents(
            child_engine=self.child_engine,
            dispatcher=self._dispatcher,
            sessions=self._sessions,
            thread_binding=thread_binding,
            modes=modes,
            profiles=self._profiles,
            plugin_host=plugin_host,
            mode_snapshots=mode_snapshots,
        )
        return subagents

    def __call__(self, selected: ModeSnapshot) -> RestrictedDispatcher:
        restricted = RestrictedDispatcher(
            self._dispatcher,
            self._tools(selected),
            allow_delegation=selected.topology is AgentTopology.TEAM,
            allow_coordination=True,
            compact_tools=True,
        )
        return self._plugin_bindings.bind(
            restricted,
            lambda selected=selected: self._tools(selected),
        )

    def _tools(self, selected: ModeSnapshot) -> tuple[str, ...]:
        coordination = tuple(
            tool.name
            for tool in self._dispatcher.tools()
            if tool.name in {"list_agents", "send_message"}
        )
        if self._is_plugin_mode(selected):
            return _unique_names(selected.definition.tool_names + coordination)
        plugin_names = tuple(tool.name for tool in self._plugin_bridge.definitions())
        return _unique_names(
            main_tools_for_mode(selected.definition.tool_names, plugin_names)
            + self._mcp_names
            + coordination
        )

    def _is_plugin_mode(self, selected: ModeSnapshot) -> bool:
        return (
            selected.digest in self.plugin_mode_digests
            or self._base_definitions.get(selected.definition.mode)
            != selected.definition
        )


def _unique_names(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(names))
