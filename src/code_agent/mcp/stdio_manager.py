from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from .registry import McpServer, McpTool


class McpSdkAdapter:
    """Boundary for the official MCP SDK; implementations own protocol details."""
    async def start(self, server: McpServer) -> None: raise NotImplementedError
    async def list_tools(self) -> tuple[McpTool, ...]: raise NotImplementedError
    async def call(self, name: str, arguments: Mapping[str, object]) -> object: raise NotImplementedError
    async def cancel(self) -> None: raise NotImplementedError
    async def close(self) -> None: raise NotImplementedError


AdapterFactory = Callable[[McpServer], McpSdkAdapter]


@dataclass(frozen=True)
class McpHealth:
    server: str
    healthy: bool
    error_type: str | None = None


class StdioMcpManager:
    """Manage bounded SDK lifecycle without parsing MCP JSON-RPC in this project."""
    def __init__(self, factory: AdapterFactory, *, start_timeout_s: float = 15.0, call_timeout_s: float = 60.0) -> None:
        self._factory, self._start_timeout_s, self._call_timeout_s, self._adapters = factory, start_timeout_s, call_timeout_s, {}

    async def start(self, server: McpServer) -> tuple[McpTool, ...]:
        if not server.enabled or not server.approved: raise PermissionError("MCP server is not approved and enabled")
        adapter = self._adapters.get(server.name)
        if adapter is None:
            adapter = self._factory(server)
            try:
                # MCP/AnyIO session contexts must be entered and closed by one task.
                await adapter.start(server)
                tools = await asyncio.wait_for(adapter.list_tools(), self._start_timeout_s)
            except Exception:
                await _close(adapter)
                raise
            self._adapters[server.name] = adapter
            return tools
        return await asyncio.wait_for(adapter.list_tools(), self._start_timeout_s)

    async def call(self, server: McpServer, tool: str, arguments: Mapping[str, object]) -> object:
        adapter = self._adapters.get(server.name)
        if adapter is None: raise RuntimeError("MCP server is not started")
        return await asyncio.wait_for(adapter.call(tool, arguments), self._call_timeout_s)

    async def cancel(self, server: str) -> None:
        adapter = self._adapters.get(server)
        if adapter is not None: await adapter.cancel()

    async def close(self, server: str) -> None:
        adapter = self._adapters.pop(server, None)
        if adapter is not None: await _close(adapter)

    async def aclose(self) -> None:
        await asyncio.gather(*(self.close(name) for name in tuple(self._adapters)), return_exceptions=True)

    def health(self, server: str) -> McpHealth:
        return McpHealth(server, server in self._adapters)


async def _close(adapter: McpSdkAdapter) -> None:
    try: await adapter.close()
    except Exception: pass
