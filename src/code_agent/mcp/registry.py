from __future__ import annotations

import re
import asyncio
from dataclasses import dataclass, field, replace, is_dataclass
from enum import Enum
from typing import Callable, Iterable, Mapping

from code_agent.core.models import ActionRequest, ToolDefinition
from .lifecycle_owner import McpBeforeCallError, McpBusyError


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class McpRisk(str, Enum):
    READ = "read"; WRITE = "write"; NETWORK = "network"; CRITICAL = "critical"


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: Mapping[str, object]
    risk: McpRisk

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name) or not isinstance(self.description, str) or not isinstance(self.input_schema, Mapping) or not isinstance(self.risk, McpRisk):
            raise ValueError("invalid MCP tool")


@dataclass(frozen=True)
class McpServer:
    name: str
    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None
    environment: tuple[str, ...] = ()
    enabled: bool = False
    approved: bool = False
    tools: tuple[McpTool, ...] = ()
    tool_risks: Mapping[str, McpRisk] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name) or not isinstance(self.command, str) or not self.command.strip() or not all(isinstance(item, str) and item for item in self.args) or self.cwd is not None and (not isinstance(self.cwd, str) or not self.cwd):
            raise ValueError("invalid MCP stdio server configuration")
        if not all(isinstance(item, str) and _NAME.fullmatch(item) for item in self.environment):
            raise ValueError("invalid MCP environment allowlist")
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("duplicate MCP tool")
        if not all(_NAME.fullmatch(name) and isinstance(risk, McpRisk) for name, risk in self.tool_risks.items()):
            raise ValueError("invalid MCP tool risk mapping")


@dataclass(frozen=True)
class McpSnapshot:
    generation: int
    servers: tuple[McpServer, ...]
    tools: tuple[ToolDefinition, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 0
        ):
            raise ValueError("generation must be a non-negative integer")
        object.__setattr__(self, "servers", tuple(self.servers))
        object.__setattr__(self, "tools", tuple(self.tools))


class McpRegistry:
    """Approved stdio inventory and schema-derived, policy-bound namespaces."""
    def __init__(self, servers: Iterable[McpServer] = ()) -> None:
        items = tuple(servers); self._servers = {server.name: server for server in items}
        if len(items) != len(self._servers): raise ValueError("duplicate MCP server")
    def list(self) -> tuple[McpServer, ...]: return tuple(self._servers.values())
    def status(self, name: str | None = None) -> tuple[McpServer, ...]: return self.list() if name is None else (self._servers[name],)
    def exposed_tools(self, server: str) -> tuple[McpTool, ...]:
        try: item = self._servers[server]
        except KeyError: raise ValueError("unknown MCP server") from None
        return item.tools if item.enabled and item.approved else ()
    def namespace(self, server: str, tool: str) -> str:
        if not any(item.name == tool for item in self.exposed_tools(server)): raise ValueError("unknown or unavailable MCP tool")
        return f"mcp.{server}.{tool}"
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(ToolDefinition(self.namespace(server.name, tool.name), tool.description, tool.input_schema) for server in self._servers.values() for tool in self.exposed_tools(server.name))


class McpPolicyBridge:
    """Encode a discovered MCP tool as a normal typed action request."""
    def __init__(self, registry: McpRegistry) -> None: self._registry = registry
    def request(self, request_id: str, server: str, tool: str, arguments: Mapping[str, object]) -> ActionRequest:
        namespace = self._registry.namespace(server, tool)
        return ActionRequest(request_id, namespace, dict(arguments))
    def risk_map(self) -> dict[str, str]:
        return {self._registry.namespace(server.name, tool.name): tool.risk.value for server in self._registry.list() for tool in self._registry.exposed_tools(server.name)}


class McpController:
    """Enable approved SDK servers and expose only discovered, locally mapped tools."""
    def __init__(self, registry: McpRegistry, manager: object, risks: dict[str, str] | None = None) -> None:
        self._registry, self._manager, self._risks = registry, manager, risks
        self._risk_keys: set[str] = set()
        self._generation = 0
        self._lifecycle_lock = asyncio.Lock()
        self._epochs: dict[str, int] = {}
        self._sync_risks()

    @property
    def registry(self) -> McpRegistry:
        self._reconcile_faults()
        return self._registry

    def definitions(self) -> tuple[ToolDefinition, ...]:
        self._reconcile_faults()
        return self._registry.definitions()

    def snapshot(self) -> McpSnapshot:
        self._reconcile_faults()
        return McpSnapshot(
            self._generation, self._registry.list(), self._registry.definitions()
        )

    def status(self, name: str | None = None) -> tuple[McpServer, ...]:
        self._reconcile_faults()
        return self._registry.status(name)

    def risks(self) -> dict[str, str]:
        self._reconcile_faults()
        return McpPolicyBridge(self._registry).risk_map()

    async def enable(self, name: str) -> tuple[McpTool, ...]:
        epoch = self._epochs.get(name, 0) + 1
        self._epochs[name] = epoch
        async with self._lifecycle_lock:
            server = self._registry.status(name)[0]
            starting = replace(server, enabled=True, tools=())
            try:
                tools = await self._manager.start(starting)
                if self._epochs[name] != epoch:
                    raise RuntimeError("MCP enable superseded by lifecycle change")
                # SDK self-reported risk is never authority: only local mappings publish.
                mapped = tuple(replace(tool, risk=server.tool_risks[tool.name])
                               for tool in tools if tool.name in server.tool_risks)
                self._publish(replace(starting, tools=mapped))
                return mapped
            except BaseException:
                self._publish(replace(server, enabled=False, tools=()))
                await self._manager.close(name)
                raise

    async def disable(self, name: str) -> None:
        server = self._registry.status(name)[0]
        self._epochs[name] = self._epochs.get(name, 0) + 1
        self._publish(replace(server, enabled=False, tools=()))
        cancel = getattr(self._manager, "cancel", None)
        try:
            if callable(cancel):
                await cancel(name)
        finally:
            await self._manager.close(name)

    async def call(self, namespace: str, arguments: Mapping[str, object], *, expected_generation: int | None = None, before_call: Callable[[], None] | None = None) -> object:
        self._reconcile_faults()
        if expected_generation is not None and expected_generation != self._generation:
            raise PermissionError("MCP generation changed; approval is stale")
        prefix, server, tool = namespace.split(".", 2)
        if prefix != "mcp": raise ValueError("invalid MCP namespace")
        self._registry.namespace(server, tool)
        def check() -> None:
            self._reconcile_faults()
            if expected_generation is not None and expected_generation != self._generation:
                raise PermissionError("MCP generation changed; approval is stale")
            self._registry.namespace(server, tool)
            if before_call is not None: return before_call()
        try:
            if expected_generation is not None or before_call is not None:
                return await self._manager.call(self._registry.status(server)[0], tool, arguments, before_call=check)
            return await self._manager.call(self._registry.status(server)[0], tool, arguments)
        except (McpBeforeCallError, McpBusyError):
            raise
        except BaseException:
            self._publish(replace(self._registry.status(server)[0], tools=()))
            raise

    async def aclose(self) -> None:
        for server in self._registry.list():
            self._publish(replace(server, enabled=False, tools=()))
        await self._manager.aclose()

    async def restart(self, name: str) -> tuple[McpTool, ...]:
        await self.disable(name)
        return await self.enable(name)

    def diagnose(self, name: str) -> object:
        self._registry.status(name)
        health = getattr(self._manager, "health", None)
        if callable(health):
            state = health(name)
            if is_dataclass(state): return replace(state, configured=True)
            return {**state, "configured": True} if isinstance(state, Mapping) else state
        return {
            "server": name,
            "healthy": bool(self._registry.exposed_tools(name)),
        }

    def _sync_risks(self) -> None:
        if self._risks is not None:
            for key in self._risk_keys:
                self._risks.pop(key, None)
            current = McpPolicyBridge(self._registry).risk_map()
            self._risks.update(current)
            self._risk_keys = set(current)

    def _publish(self, server: McpServer) -> None:
        self._registry = McpRegistry(tuple(server if item.name == server.name else item for item in self._registry.list()))
        self._generation += 1
        self._sync_risks()

    def _reconcile_faults(self) -> None:
        health = getattr(self._manager, "health", None)
        if not callable(health): return
        for server in self._registry.list():
            if not server.tools: continue
            state = health(server.name)
            ready = state.get("healthy", False) if isinstance(state, Mapping) else getattr(state, "healthy", False)
            if not ready: self._publish(replace(server, tools=()))
