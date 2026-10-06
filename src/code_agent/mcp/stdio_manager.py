from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .registry import McpServer, McpTool
from .lifecycle_owner import LifecycleOwner, McpBeforeCallError, McpBusyError


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
    configured: bool = False
    ready: bool = False
    last_success: float | None = None
    fault: bool = False


class StdioMcpManager:
    """Manage bounded SDK lifecycle without parsing MCP JSON-RPC in this project."""
    def __init__(self, factory: AdapterFactory, *, start_timeout_s: float = 15.0, call_timeout_s: float = 60.0, close_timeout_s: float = 6.0) -> None:
        if not all(math.isfinite(value) and value > 0 for value in (start_timeout_s, call_timeout_s, close_timeout_s)):
            raise ValueError("MCP timeouts must be positive")
        self._factory, self._start_timeout_s, self._call_timeout_s = factory, start_timeout_s, call_timeout_s
        self._close_timeout_s = close_timeout_s
        self._adapters: dict[str, LifecycleOwner] = {}
        self._last_health: dict[str, McpHealth] = {}

    async def start(self, server: McpServer) -> tuple[McpTool, ...]:
        if not server.enabled or not server.approved: raise PermissionError("MCP server is not approved and enabled")
        owner = self._adapters.get(server.name)
        if owner is not None:
            if not self.health(server.name).healthy: raise RuntimeError("MCP explicit restart required")
            return await asyncio.shield(owner.started)
        owner = LifecycleOwner(self._factory(server), server, self)
        self._adapters[server.name] = owner
        try:
            async with asyncio.timeout(self._start_timeout_s + self._close_timeout_s * 2):
                return await asyncio.shield(owner.started)
        except BaseException:
            await owner.stop()
            raise

    async def call(self, server: McpServer, tool: str, arguments: Mapping[str, object], *, before_call: Callable[[], None] | None = None) -> object:
        owner = self._adapters.get(server.name)
        if owner is None or not self.health(server.name).healthy:
            raise RuntimeError("MCP server is not ready")
        future = asyncio.get_running_loop().create_future()
        if before_call is not None and not callable(before_call):
            raise TypeError("MCP before_call must be callable")
        try: owner.queue.put_nowait(((tool, dict(arguments), before_call), future))
        except asyncio.QueueFull: raise McpBusyError("MCP server is busy") from None
        try:
            async with asyncio.timeout(self._call_timeout_s * 2):
                return await asyncio.shield(future)
        except (McpBeforeCallError, McpBusyError):
            raise
        except BaseException as error:
            future.cancel()
            if owner.error is None: owner.error = type(error).__name__
            await owner.stop()
            raise

    async def cancel(self, server: str) -> None:
        owner = self._adapters.get(server)
        if owner is not None: await owner.stop()

    async def close(self, server: str) -> None:
        owner = self._adapters.get(server)
        if owner is not None:
            await owner.stop()
            self._last_health[server] = self.health(server)
            self._adapters.pop(server, None)

    async def aclose(self) -> None:
        await asyncio.gather(*(self.close(name) for name in tuple(self._adapters)))

    def health(self, server: str) -> McpHealth:
        owner = self._adapters.get(server)
        if owner is None: return self._last_health.get(server, McpHealth(server, False))
        ready = owner.ready and not owner.task.done() and not owner.stopping
        error = owner.cleanup_error or owner.error
        return McpHealth(server, ready, error, True, ready, owner.last_success, error is not None)
