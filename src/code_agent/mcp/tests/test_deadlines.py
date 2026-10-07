from __future__ import annotations

import asyncio
import unittest
from contextlib import AsyncExitStack

import anyio

from code_agent.mcp.deadlines import timeout
from code_agent.mcp.registry import McpServer
from code_agent.mcp.stdio_manager import McpSdkAdapter, StdioMcpManager


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_deadline_runs_and_cleans_in_current_task(self):
        owner = asyncio.current_task()
        cleaned = []
        with self.assertRaises(TimeoutError) as raised:
            async with timeout(.02):
                try:
                    self.assertIs(asyncio.current_task(), owner)
                    await asyncio.sleep(10)
                finally:
                    cleaned.append(asyncio.current_task())
        self.assertIs(type(raised.exception), TimeoutError)
        self.assertEqual(cleaned, [owner])
        await asyncio.sleep(0)

    async def test_external_cancel_remains_cancelled_error(self):
        entered = asyncio.Event()
        cleaned = asyncio.Event()

        async def operation():
            async with timeout(10):
                entered.set()
                try:
                    await asyncio.sleep(10)
                finally:
                    cleaned.set()

        task = asyncio.create_task(operation())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned.is_set())

    async def test_nested_inner_deadline_keeps_outer_usable(self):
        async with timeout(1):
            with self.assertRaises(TimeoutError):
                async with timeout(.02):
                    await asyncio.sleep(10)
            await asyncio.sleep(0)

    async def test_outer_deadline_is_not_claimed_by_inner(self):
        with self.assertRaises(TimeoutError):
            async with timeout(.02):
                async with timeout(1):
                    await asyncio.sleep(10)

    async def test_normal_exit_removes_pending_deadline(self):
        async with timeout(.02):
            await asyncio.sleep(0)
        await asyncio.sleep(.04)

    async def test_persistent_sdk_scope_and_idle_queue_keep_same_owner(self):
        # Like the real SDK, start leaves a task group scope in its exit stack.
        # A surrounding AnyIO fail_after would exit out of scope order here.
        class Adapter(McpSdkAdapter):
            def __init__(self):
                self.stack = AsyncExitStack()
                self.tasks = []
                self.pinged = asyncio.Event()

            async def start(self, server):
                self.tasks.append(asyncio.current_task())
                await self.stack.enter_async_context(anyio.create_task_group())

            async def list_tools(self):
                self.tasks.append(asyncio.current_task())
                return ()

            async def ping(self):
                self.tasks.append(asyncio.current_task())
                self.pinged.set()

            async def call(self, name, arguments):
                self.tasks.append(asyncio.current_task())
                return "result"

            async def cancel(self):
                self.tasks.append(asyncio.current_task())
                await self.stack.aclose()

            async def close(self):
                self.tasks.append(asyncio.current_task())
                await self.stack.aclose()

        adapter = Adapter()
        server = McpServer("scope-fixture", "unused", enabled=True, approved=True)
        manager = StdioMcpManager(lambda _: adapter, start_timeout_s=1,
                                  call_timeout_s=1, close_timeout_s=1)
        try:
            await manager.start(server)
            async with timeout(2):
                await adapter.pinged.wait()
            self.assertTrue(manager.health(server.name).ready)
            self.assertEqual(await manager.call(server, "operation", {}), "result")
            owner = manager._adapters[server.name].task
            await asyncio.create_task(manager.close(server.name))
            self.assertFalse(manager.health(server.name).fault)
            self.assertTrue(all(task is owner for task in adapter.tasks))
        finally:
            await manager.aclose()


if __name__ == "__main__":
    unittest.main()
