"""The one task that enters, operates and exits official SDK contexts."""
from __future__ import annotations

import asyncio
import time


class McpBeforeCallError(PermissionError):
    """A queued action lost authorization before any SDK call began."""


class McpBusyError(RuntimeError):
    """The bounded queue rejected this action before any SDK call began."""


class LifecycleOwner:
    def __init__(self, adapter, server, manager) -> None:
        self.adapter, self.server, self.manager = adapter, server, manager
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self.started = asyncio.get_running_loop().create_future()
        self.started.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        self.ready, self.last_success, self.error = False, None, None
        self.cleanup_error = None
        self.stopping = False
        self.cleaning = False
        self.task = asyncio.create_task(self.run(), name=f"mcp-owner:{server.name}")
        self.watchdog = asyncio.create_task(self.watch_startup(), name=f"mcp-startup-watchdog:{server.name}")

    async def watch_startup(self) -> None:
        try:
            async with asyncio.timeout(self.manager._start_timeout_s + self.manager._close_timeout_s):
                await asyncio.shield(self.started)
        except TimeoutError:
            await self.terminate_on_deadline()
        except BaseException:
            pass

    async def watch_close(self) -> None:
        try:
            async with asyncio.timeout(self.manager._close_timeout_s):
                await asyncio.shield(self.task)
        except TimeoutError:
            await self.terminate_on_deadline()
        except BaseException:
            pass

    async def terminate_on_deadline(self) -> None:
        terminate = getattr(self.adapter, "terminate_owned_process", None)
        if callable(terminate):
            try:
                async with asyncio.timeout(self.manager._close_timeout_s):
                    await terminate()
            except Exception as error:
                # This task owns observing fallback failures; don't report a
                # clean shutdown or leave an unobserved background exception.
                self.cleanup_error = type(error).__name__

    async def run(self) -> None:
        current = None
        try:
            async with asyncio.timeout(self.manager._start_timeout_s):
                await self.adapter.start(self.server)
                tools = await self.adapter.list_tools()
            self.ready, self.last_success = True, time.time()
            self.started.set_result(tools)
            if self.stopping: return
            while True:
                try:
                    command, current = await asyncio.wait_for(self.queue.get(), 0.5)
                except TimeoutError:
                    ping = getattr(self.adapter, "ping", None)
                    if callable(ping):
                        async with asyncio.timeout(self.manager._call_timeout_s):
                            await ping()
                        self.last_success = time.time()
                    continue
                name, arguments, before_call = command
                if before_call is not None:
                    try:
                        result = before_call()
                        if result is not None:
                            # The trusted callback is synchronous, never a new
                            # asynchronous execution path or approval mechanism.
                            close = getattr(result, "close", None)
                            if callable(close): close()
                            raise TypeError("MCP before_call must return None")
                    except Exception:
                        if not current.done():
                            current.set_exception(McpBeforeCallError("MCP queued action no longer authorized"))
                        current = None
                        continue
                async with asyncio.timeout(self.manager._call_timeout_s):
                    result = await self.adapter.call(name, arguments)
                self.last_success = time.time()
                if not current.done(): current.set_result(result)
                current = None
        except BaseException as error:
            if not self.stopping: self.error = type(error).__name__
            for future in (self.started, current):
                if future is not None and not future.done(): future.set_exception(error)
        finally:
            self.cleaning = True
            self.ready = False
            # The SDK's own graceful-close default may exceed our deadline.
            # If cancellation interrupts its finally, reap the same SDK handle
            # while this original owner remains responsible for context exits.
            self.close_watchdog = asyncio.create_task(self.watch_close(),
                name=f"mcp-close-watchdog:{self.server.name}")
            try:
                async with asyncio.timeout(self.manager._close_timeout_s):
                    await self.adapter.cancel()
                    await self.adapter.close()
            except BaseException as error:
                self.error = type(error).__name__
            while not self.queue.empty():
                _, future = self.queue.get_nowait()
                if not future.done(): future.set_exception(RuntimeError("MCP connection closed"))

    async def stop(self) -> None:
        self.ready = False
        if not self.stopping:
            self.stopping = True
            # Do not interrupt SDK transport __aenter__: its process context can
            # wait without the shutdown finally having been entered yet. Startup
            # has its own deadline; cancellation waits for that bounded phase.
            if self.started.done() and not self.task.done() and not self.cleaning:
                self.task.cancel()
        # Caller cancellation cannot transfer SDK cleanup ownership.
        try:
            async with asyncio.timeout(self.manager._start_timeout_s + self.manager._close_timeout_s * 2):
                await asyncio.shield(self.task)
        except asyncio.CancelledError:
            if not self.task.done() or not self.task.cancelled(): raise
