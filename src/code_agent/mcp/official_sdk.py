from __future__ import annotations

import os
from contextlib import AsyncExitStack
from collections.abc import Mapping

from .registry import McpRisk, McpServer, McpTool
from .stdio_manager import McpSdkAdapter


class OfficialMcpSdkAdapter(McpSdkAdapter):
    """Official Python MCP SDK adapter; imported only when a server is started."""

    def __init__(self, *, tool_risks: Mapping[str, McpRisk]) -> None:
        self._tool_risks, self._stack, self._session = dict(tool_risks), None, None
        self._transport = None

    async def start(self, server: McpServer) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as error:
            raise RuntimeError("official MCP SDK is not installed") from error
        environment = {name: os.environ[name] for name in server.environment if name in os.environ}
        parameters = StdioServerParameters(command=server.command, args=list(server.args), env=environment, cwd=server.cwd)
        stack = AsyncExitStack()
        self._stack = stack
        stderr = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
        try:
            self._transport = stdio_client(parameters, errlog=stderr)
            read, write = await stack.enter_async_context(self._transport)
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            # The lifecycle owner closes even a partially entered stack with its
            # shutdown deadline, in the same task that entered SDK contexts.
            raise
        self._stack, self._session = stack, session

    async def list_tools(self) -> tuple[McpTool, ...]:
        if self._session is None: raise RuntimeError("MCP SDK session is not started")
        result = await self._session.list_tools()
        tools: list[McpTool] = []
        for tool in result.tools:
            risk = self._tool_risks.get(tool.name)
            if risk is None: continue
            schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
            if not isinstance(schema, Mapping): continue
            tools.append(McpTool(tool.name, tool.description or tool.name, schema, risk))
        return tuple(tools)

    async def call(self, name: str, arguments: Mapping[str, object]) -> object:
        if self._session is None: raise RuntimeError("MCP SDK session is not started")
        result = await self._session.call_tool(name, arguments)
        dump = getattr(result, "model_dump", None)
        return dump(mode="json") if callable(dump) else result

    async def cancel(self) -> None:
        # Closing the SDK session is the portable cancellation mechanism for stdio calls.
        await self.close()

    async def ping(self) -> None:
        if self._session is None: raise RuntimeError("MCP SDK session is not started")
        await self._session.send_ping()

    async def terminate_owned_process(self) -> None:
        """Locked SDK 1.29.1 fallback for a cancelled transport enter/exit.

        SDK exposes no process handle publicly. Only this adapter's still-active
        stdio generator owns the handle; no PID enumeration or protocol parsing.
        The original lifecycle task remains responsible for context exits.
        """
        from mcp.client.stdio import _terminate_process_tree
        generator = getattr(self._transport, "gen", None)
        frame = getattr(generator, "ag_frame", None)
        process = frame.f_locals.get("process") if frame is not None else None
        if process is not None:
            streams = tuple(frame.f_locals.get(name) for name in
                            ("read_stream", "read_stream_writer", "write_stream", "write_stream_reader"))
            # The configured grace period has already expired. Do not add the
            # SDK's default POSIX grace period before its force escalation.
            await _terminate_process_tree(process, timeout_seconds=0)
            # SDK's pre-yield failure skips its shutdown finally, including the
            # memory-stream closes. Stream aclose owns no cancellation context.
            for stream in streams:
                if stream is not None: await stream.aclose()

    async def close(self) -> None:
        if self._stack is not None:
            stack, self._stack, self._session = self._stack, None, None
            await stack.aclose()
