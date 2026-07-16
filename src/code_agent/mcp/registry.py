from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable, Mapping

from code_agent.core.models import ActionRequest, ToolDefinition


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
        self._sync_risks()

    @property
    def registry(self) -> McpRegistry:
        return self._registry

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._registry.definitions()

    def status(self, name: str | None = None) -> tuple[McpServer, ...]:
        return self._registry.status(name)

    def risks(self) -> dict[str, str]:
        return McpPolicyBridge(self._registry).risk_map()

    async def enable(self, name: str) -> tuple[McpTool, ...]:
        server = self._registry.status(name)[0]
        tools = await self._manager.start(server)
        updated = replace(server, tools=tools)
        self._registry = McpRegistry(tuple(updated if item.name == name else item for item in self._registry.list()))
        self._sync_risks()
        return tools

    async def disable(self, name: str) -> None:
        await self._manager.close(name)
        server = self._registry.status(name)[0]
        updated = replace(server, tools=())
        self._registry = McpRegistry(tuple(updated if item.name == name else item for item in self._registry.list()))
        self._sync_risks()

    async def call(self, namespace: str, arguments: Mapping[str, object]) -> object:
        prefix, server, tool = namespace.split(".", 2)
        if prefix != "mcp": raise ValueError("invalid MCP namespace")
        self._registry.namespace(server, tool)
        return await self._manager.call(self._registry.status(server)[0], tool, arguments)

    async def aclose(self) -> None:
        await self._manager.aclose()

    async def restart(self, name: str) -> tuple[McpTool, ...]:
        await self.disable(name)
        return await self.enable(name)

    def _sync_risks(self) -> None:
        if self._risks is not None:
            self._risks.clear(); self._risks.update(self.risks())
