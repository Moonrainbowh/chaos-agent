from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class McpRisk(str, Enum):
    READ = "read"; WRITE = "write"; NETWORK = "network"; CRITICAL = "critical"


@dataclass(frozen=True)
class McpServer:
    name: str
    transport: str
    reference: str
    enabled: bool = False
    approved: bool = False

    def __post_init__(self) -> None:
        if not self.name.replace("-", "").replace("_", "").isalnum() or self.transport not in {"stdio", "http"} or not self.reference: raise ValueError("invalid MCP server configuration")


class McpRegistry:
    """Configured server inventory; transport lifecycle belongs to a later policy bridge."""
    def __init__(self, servers: Iterable[McpServer] = ()) -> None:
        self._servers = {server.name: server for server in servers}
    def list(self) -> tuple[McpServer, ...]: return tuple(self._servers.values())
    def status(self, name: str | None = None) -> tuple[McpServer, ...]: return self.list() if name is None else (self._servers[name],)
    def namespace(self, server: str, tool: str) -> str:
        if server not in self._servers or not tool.replace("_", "").isalnum(): raise ValueError("unknown MCP tool")
        return f"mcp.{server}.{tool}"
