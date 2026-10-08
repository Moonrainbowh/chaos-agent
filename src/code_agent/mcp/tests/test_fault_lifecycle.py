from __future__ import annotations

import asyncio
import sys
import tempfile
import time
import gc
import unittest
from unittest.mock import patch
from pathlib import Path

import psutil

from code_agent.mcp.official_sdk import OfficialMcpSdkAdapter
from code_agent.mcp.registry import McpController, McpRegistry, McpRisk, McpServer
from code_agent.mcp.stdio_manager import StdioMcpManager, McpSdkAdapter
from code_agent.mcp.lifecycle_owner import McpBeforeCallError, McpBusyError


class RealFaultLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Resolve client SDK modules before starting fault-response deadlines.
        # The real child process still imports its SDK and handshakes within
        # the unchanged 1.5s budget; cold default startup is checked separately.
        from mcp import ClientSession  # noqa: F401
        self.temporary = tempfile.TemporaryDirectory(prefix="s13-mcp-")
        self.root = Path(self.temporary.name).resolve()
        assert self.root.is_absolute() and self.root.name.startswith("s13-mcp-")
        self.manager = StdioMcpManager(lambda item: OfficialMcpSdkAdapter(tool_risks=item.tool_risks),
            start_timeout_s=1.5, call_timeout_s=0.4, close_timeout_s=6)

    async def asyncTearDown(self):
        await self.manager.aclose()
        # The SDK is responsible for cleanup; this assertion precedes fixture removal.
        self.assert_child_stopped()
        self.temporary.cleanup()

    def server(self, mode):
        return McpServer("fixture", sys.executable,
            (str(Path(__file__).with_name("fault_fixture_server.py")), str(self.root), mode),
            approved=True, enabled=True, tool_risks={"operation": McpRisk.WRITE})

    def assert_child_stopped(self):
        pid_path = self.root / "pid"
        if not pid_path.exists(): return
        pid = int(pid_path.read_text())
        try:
            process = psutil.Process(pid)
            # A PID reuse is not our process; only the exact fixture directory binds it.
            if str(self.root) in process.cmdline():
                process.wait(timeout=1)
        except psutil.NoSuchProcess:
            return
        except psutil.TimeoutExpired:
            self.fail("official SDK left owned fixture process alive")

    async def test_handshake_timeout_cleans_official_process(self):
        began = time.monotonic()
        with self.assertRaises(TimeoutError): await self.manager.start(self.server("handshake"))
        self.assertLess(time.monotonic() - began, 8)
        self.assertFalse(self.manager.health("fixture").ready)
        self.assertEqual(self.manager.health("fixture").error_type, "TimeoutError")
        self.assert_child_stopped()

    async def test_tool_discovery_timeout_cleans_official_process(self):
        with self.assertRaises(TimeoutError): await self.manager.start(self.server("discovery"))
        self.assertFalse(self.manager.health("fixture").ready)
        self.assert_child_stopped()

    async def test_idle_service_exit_is_detected_by_sdk_ping(self):
        await self.manager.start(self.server("idle_exit"))
        deadline = time.monotonic() + 4
        while self.manager.health("fixture").ready and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        state = self.manager.health("fixture")
        self.assertFalse(state.ready)
        self.assertTrue(state.fault)
        self.assertIsNotNone(state.last_success)
        self.assert_child_stopped()

    async def test_cross_task_shutdown_of_uncooperative_process(self):
        await asyncio.create_task(self.manager.start(self.server("close")))
        began = time.monotonic()
        await asyncio.create_task(self.manager.close("fixture"))
        self.assertLess(time.monotonic() - began, 7)
        self.assert_child_stopped()

    async def test_short_close_deadline_reaps_sdk_process(self):
        await self.manager.start(self.server("close"))
        self.manager._close_timeout_s = 0.2
        began = time.monotonic()
        await self.manager.close("fixture")
        self.assertLess(time.monotonic() - began, 2)
        self.assertFalse(self.manager.health("fixture").ready)
        self.assert_child_stopped()

    async def test_call_timeout_does_not_replay_unknown_write(self):
        server = self.server("call")
        await self.manager.start(server)
        with self.assertRaises(TimeoutError): await self.manager.call(server, "operation", {})
        self.assertFalse(self.manager.health("fixture").ready)
        with self.assertRaises(RuntimeError): await self.manager.call(server, "operation", {})
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call"])
        self.assert_child_stopped()

    async def test_service_exit_withdraws_generation_and_local_risk(self):
        server = self.server("exit")
        risks = {"file.write": "write"}
        controller = McpController(McpRegistry((server,)), self.manager, risks)
        await controller.enable(server.name)
        generation = controller.snapshot().generation
        with self.assertRaises(Exception):
            await controller.call("mcp.fixture.operation", {}, expected_generation=generation)
        self.assertEqual(controller.snapshot().tools, ())
        self.assertGreater(controller.snapshot().generation, generation)
        self.assertEqual(risks, {"file.write": "write"})
        self.assertFalse(controller.diagnose(server.name).ready)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call"])

    async def test_double_cancel_preserves_owner_cleanup(self):
        server = self.server("call")
        await self.manager.start(server)
        call = asyncio.create_task(self.manager.call(server, "operation", {}))
        while not (self.root / "calls").exists(): await asyncio.sleep(0.01)
        call.cancel()
        await asyncio.sleep(0)
        call.cancel()
        with self.assertRaises(asyncio.CancelledError): await call
        await self.manager.close(server.name)
        self.assert_child_stopped()
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call"])

    async def test_missing_executable_start_failure_is_bounded(self):
        server = McpServer("missing", str(self.root / "nonexistent.exe"), approved=True, enabled=True)
        with self.assertRaises(Exception): await self.manager.start(server)
        self.assertFalse(self.manager.health("missing").ready)

    async def test_cancel_during_handshake_keeps_cleanup_in_owner(self):
        starting = asyncio.create_task(self.manager.start(self.server("handshake")))
        deadline = time.monotonic() + 4
        while not (self.root / "pid").exists() and not starting.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertTrue((self.root / "pid").exists(), "fixture startup did not reach explicit PID marker")
        starting.cancel()
        await asyncio.sleep(0)
        starting.cancel()
        with self.assertRaises(asyncio.CancelledError): await starting
        await self.manager.close("fixture")
        self.assert_child_stopped()

    async def test_startup_deadline_in_transport_enter_reaps_sdk_process(self):
        # Inject at the SDK's real process resource entry, after spawning the
        # actual child and before stdio yields. The official SDK owns transport.
        from anyio.abc import Process
        marker = asyncio.Event()
        original_enter = Process.__aenter__
        async def delayed_enter(process):
            await original_enter(process)
            marker.set()
            try:
                await asyncio.sleep(120)
            except asyncio.CancelledError:
                # Reproduce SDK cancellation cleanup waiting on an unresponsive
                # owned process before the stdio shutdown finally is established.
                await process.aclose()
                raise
            return process
        self.manager._start_timeout_s = 0.5
        self.manager._close_timeout_s = 3
        began = time.monotonic()
        with patch.object(Process, "__aenter__", delayed_enter):
            with self.assertRaises(TimeoutError): await self.manager.start(self.server("handshake"))
        self.assertTrue(marker.is_set())
        self.assertLess(time.monotonic() - began, 7)
        self.assert_child_stopped()

    async def queued_fixture(self, *, invalidate_generation: bool):
        self.manager._call_timeout_s = 3
        server = self.server("queue")
        other = McpServer("other", sys.executable, approved=True)
        controller = McpController(McpRegistry((server, other)), self.manager)
        await controller.enable("fixture")
        generation = controller.snapshot().generation
        first = asyncio.create_task(controller.call("mcp.fixture.operation", {"sequence": 0}))
        deadline = time.monotonic() + 3
        while not (self.root / "calls").exists() and time.monotonic() < deadline:
            await asyncio.sleep(.01)
        self.assertTrue((self.root / "calls").exists())
        authorized = True
        def source_check():
            if not authorized: raise RuntimeError("source revoked")
        second = asyncio.create_task(controller.call("mcp.fixture.operation", {"sequence": 1},
            expected_generation=generation, before_call=source_check))
        owner = self.manager._adapters["fixture"]
        deadline = time.monotonic() + 3
        while owner.queue.empty() and time.monotonic() < deadline: await asyncio.sleep(.01)
        self.assertEqual(owner.queue.qsize(), 1)
        if invalidate_generation:
            await controller.disable("other")
        else:
            authorized = False
        (self.root / "release").write_text("release", encoding="utf-8")
        await first
        with self.assertRaises(McpBeforeCallError): await second
        self.assertTrue(self.manager.health("fixture").ready)
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call"])
        self.assertEqual(len(controller.definitions()), 1)
        await controller.call("mcp.fixture.operation", {"sequence": 2})
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call", "call"])

    async def test_revoked_source_is_checked_after_real_sdk_queue(self):
        await self.queued_fixture(invalidate_generation=False)

    async def test_generation_is_checked_after_real_sdk_queue(self):
        await self.queued_fixture(invalidate_generation=True)

    async def test_busy_rejection_preserves_already_approved_queue_and_generation(self):
        self.manager._call_timeout_s = 3
        controller = McpController(McpRegistry((self.server("queue"),)), self.manager)
        await controller.enable("fixture")
        snapshot = controller.snapshot()
        first = asyncio.create_task(controller.call("mcp.fixture.operation", {"sequence": 0},
            expected_generation=snapshot.generation))
        deadline = time.monotonic() + 3
        while not (self.root / "calls").exists() and time.monotonic() < deadline:
            await asyncio.sleep(.01)
        self.assertTrue((self.root / "calls").exists())
        second = asyncio.create_task(controller.call("mcp.fixture.operation", {"sequence": 1},
            expected_generation=snapshot.generation))
        owner = self.manager._adapters["fixture"]
        deadline = time.monotonic() + 3
        while owner.queue.empty() and time.monotonic() < deadline: await asyncio.sleep(.01)
        self.assertEqual(owner.queue.qsize(), 1)
        with self.assertRaises(McpBusyError):
            await controller.call("mcp.fixture.operation", {"sequence": 2},
                expected_generation=snapshot.generation)
        self.assertEqual(controller.snapshot(), snapshot)
        self.assertEqual(controller.risks(), {"mcp.fixture.operation": "write"})
        self.assertTrue(self.manager.health("fixture").ready)
        (self.root / "release").write_text("release", encoding="utf-8")
        await first
        await second
        self.assertEqual((self.root / "calls").read_text().splitlines(), ["call", "call"])
        self.assertEqual(controller.snapshot(), snapshot)

    async def watchdog_failure(self, phase):
        class Adapter(McpSdkAdapter):
            async def start(self, server):
                if phase == "startup":
                    try: await asyncio.sleep(.5)
                    except asyncio.CancelledError:
                        await asyncio.sleep(.2)
                        raise
            async def list_tools(self): return ()
            async def cancel(self):
                if phase == "close":
                    try: await asyncio.sleep(.5)
                    except asyncio.CancelledError: await asyncio.sleep(.2)
            async def close(self): pass
            async def terminate_owned_process(self): raise RuntimeError("fixed cleanup failure")
        self.manager = StdioMcpManager(lambda _: Adapter(), start_timeout_s=.05,
            call_timeout_s=.05, close_timeout_s=.05)
        server = McpServer("fixture", "unused-fixture", enabled=True, approved=True)
        messages = []
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: messages.append(context.get("message")))
        try:
            try: await self.manager.start(server)
            except TimeoutError: pass
            if phase == "close":
                try: await self.manager.close("fixture")
                except TimeoutError: pass
            await asyncio.sleep(.3)
            await self.manager.aclose()
            gc.collect()
            await asyncio.sleep(0)
            self.assertEqual(self.manager.health("fixture").error_type, "RuntimeError")
            self.assertTrue(self.manager.health("fixture").fault)
            self.assertEqual(messages, [])
        finally: loop.set_exception_handler(previous)

    async def test_startup_watchdog_failure_is_observed_and_diagnosed(self):
        await self.watchdog_failure("startup")

    async def test_close_watchdog_failure_is_observed_and_diagnosed(self):
        await self.watchdog_failure("close")


if __name__ == "__main__": unittest.main()
