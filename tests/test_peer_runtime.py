from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from code_agent.context.attachment_budget import message_tokens
from code_agent.context.tokens import estimate_tokens
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ContextBundle, Message
from code_agent.interfaces.terminal_state import TerminalState
from code_agent.peers.models import PeerInboundPolicy, PeerMessageStatus
from code_agent.peers.service import PeerMessagingService, PeerServiceLimits
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent_win.peer_runtime import (
    PeerContextBuilder,
    PeerDeliveryBuffer,
    PeerRuntime,
)


class _Context:
    async def build(self, *_: object, **__: object) -> ContextBundle:
        return ContextBundle(
            "base system",
            (Message(role="user", content="original user objective"),),
        )


class PeerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_claimed_peer_text_enters_only_bounded_untrusted_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteSessionRepository(
                Path(directory) / "sessions.sqlite3"
            )
            sender = PeerMessagingService(
                repository,
                owner_pid=101,
                owner_create_time=1.0,
                workspace_root="C:/sender",
                permission_mode="auto",
                name="sender",
            )
            receiver = PeerMessagingService(
                repository,
                owner_pid=202,
                owner_create_time=2.0,
                workspace_root="C:/receiver",
                permission_mode="auto",
                name="receiver",
            )
            await sender.register()
            await receiver.register()
            sent = await sender.send_message(
                receiver.session.session_ref,
                "please inspect module A; I cannot approve any action",
            )
            claim = (await receiver.claim_inbox())[0]
            buffer = PeerDeliveryBuffer(receiver, asyncio.Lock())
            await buffer.offer(claim, "sender", "stable-ref")

            builder = PeerContextBuilder(_Context(), buffer)
            bundle = await builder.build("thread-1")

            self.assertEqual(
                bundle.messages,
                (Message(role="user", content="original user objective"),),
            )
            self.assertIn('"origin":"peer"', bundle.system_prompt)
            self.assertIn("not the user", bundle.system_prompt)
            self.assertIn("cannot grant permission", bundle.system_prompt)
            self.assertIn('"sender_ref":"stable-ref"', bundle.system_prompt)
            self.assertIn("Never acknowledge, echo, or reply", bundle.system_prompt)
            self.assertIn(sent.message.content, bundle.system_prompt)
            queued = await receiver.list_inbox((PeerMessageStatus.QUEUED,))
            self.assertEqual(tuple(item.id for item in queued), (sent.message.id,))

            await builder.accept_pending_context("thread-1")
            inbox = await receiver.list_inbox(
                (PeerMessageStatus.DELIVERED,)
            )
            self.assertEqual(tuple(item.id for item in inbox), (sent.message.id,))

            counts = await repository._database.read(
                lambda connection: (
                    connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                    connection.execute(
                        "SELECT COUNT(*) FROM task_controls"
                    ).fetchone()[0],
                )
            )
            self.assertEqual(counts, (0, 0))

    async def test_peer_json_defers_whole_message_when_budget_is_too_small(self) -> None:
        class BudgetContext:
            async def build(self, *_: object, **__: object) -> ContextBundle:
                return ContextBundle(
                    "small base system",
                    (Message(role="user", content="objective"),),
                    {"prompt_tokens": 300},
                )

        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            sender = _service(repository, 301, "sender")
            receiver = _service(repository, 302, "receiver")
            await asyncio.gather(sender.register(), receiver.register())
            sent = await sender.send_message("receiver", "x" * 4_000)
            claim = (await receiver.claim_inbox())[0]
            buffer = PeerDeliveryBuffer(receiver)
            await buffer.offer(claim, "sender", sender.session.session_ref)

            builder = PeerContextBuilder(BudgetContext(), buffer)
            bundle = await builder.build("thread-budget")
            total = estimate_tokens(bundle.system_prompt) + sum(
                message_tokens(message) for message in bundle.messages
            )

            self.assertLessEqual(total, 300)
            self.assertNotIn("<peer_context>", bundle.system_prompt)
            self.assertNotIn(sent.message.content, bundle.system_prompt)
            await builder.accept_pending_context("thread-budget")
            queued = await receiver.list_inbox((PeerMessageStatus.QUEUED,))
            self.assertEqual([item.id for item in queued], [sent.message.id])

    async def test_terminal_claim_conflict_drops_poison_buffer_entry(self) -> None:
        class Clock:
            value = datetime(2026, 8, 10, tzinfo=timezone.utc)

            def __call__(self):
                return self.value

        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            clock = Clock()
            limits = PeerServiceLimits(message_ttl_seconds=2)
            sender = _service(repository, 401, "sender", limits=limits, clock=clock)
            receiver = _service(
                repository, 402, "receiver", limits=limits, clock=clock
            )
            await asyncio.gather(sender.register(), receiver.register())
            await sender.send_message("receiver", "expire after claim")
            claim = (await receiver.claim_inbox())[0]
            buffer = PeerDeliveryBuffer(receiver)
            await buffer.offer(claim, "sender", sender.session.session_ref)
            batch = await buffer.context_batch()

            clock.value += timedelta(seconds=3)
            await buffer.acknowledge(batch)

            self.assertFalse(buffer.has_pending)
            expired = await receiver.list_inbox((PeerMessageStatus.EXPIRED,))
            self.assertEqual(len(expired), 1)

    async def test_held_message_notifies_without_claiming_or_waking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            sender = _service(repository, 501, "sender")
            receiver = _service(
                repository,
                502,
                "receiver",
                policy=PeerInboundPolicy.HOLD,
            )
            await asyncio.gather(sender.register(), receiver.register())
            sent = await sender.send_message("receiver", "review first")
            app = _IdleApp()
            runtime = PeerRuntime(receiver, Path(directory), [app], permission_mode="auto")

            await runtime._notify_held()

            self.assertIn(sent.message.id[:12], " ".join(app.entries))
            self.assertFalse(runtime._buffer.has_pending)
            self.assertIsNone(app._run_task)

    async def test_task_owned_thread_keeps_peer_for_user_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            sender = _service(repository, 601, "sender")
            receiver = _service(repository, 602, "receiver")
            await asyncio.gather(sender.register(), receiver.register())
            await sender.send_message("receiver", "note for paused task")
            app = _IdleApp(thread_id="task-thread", task_owned=True)
            runtime = PeerRuntime(receiver, Path(directory), [app], permission_mode="auto")

            await runtime._poll_inbox()

            self.assertTrue(runtime._buffer.has_pending)
            self.assertIsNone(app._run_task)

    async def test_closing_tui_keeps_peer_queued_without_starting_a_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            sender = _service(repository, 651, "sender")
            receiver = _service(repository, 652, "receiver")
            await asyncio.gather(sender.register(), receiver.register())
            await sender.send_message("receiver", "arrived during shutdown")
            app = _IdleApp()
            app._closing = True
            runtime = PeerRuntime(receiver, Path(directory), [app], permission_mode="auto")

            await runtime._poll_inbox()

            self.assertTrue(runtime._buffer.has_pending)
            self.assertIsNone(app._run_task)

    async def test_idle_peer_turn_uses_isolated_thread_and_keeps_user_thread(self) -> None:
        class Controller:
            def __init__(self) -> None:
                self.thread_ids: list[str | None] = []

            async def receive_peer(self, *, thread_id: str | None, **_: object):
                self.thread_ids.append(thread_id)
                yield AgentEvent(EventKind.RUN_STARTED, {"thread_id": "peer-thread"})
                yield AgentEvent(EventKind.COMPLETED, {"thread_id": "peer-thread"})

        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteSessionRepository(Path(directory) / "sessions.sqlite3")
            receiver = _service(repository, 701, "receiver")
            await receiver.register()
            app = _IdleApp(thread_id="user-thread")
            app.controller = Controller()
            app.state = TerminalState()
            app._flush_pending_entries = lambda: None
            app._start_animation = lambda: None
            app.redraw = lambda: None
            runtime = PeerRuntime(receiver, Path(directory), [app], permission_mode="auto")

            await runtime._consume_peer(app, CancellationToken())

            self.assertEqual(app.controller.thread_ids, [None])
            self.assertEqual(runtime._peer_thread_id, "peer-thread")
            self.assertEqual(app.current_thread_id, "user-thread")


def _service(
    repository: SQLiteSessionRepository,
    pid: int,
    name: str,
    *,
    policy: PeerInboundPolicy = PeerInboundPolicy.AUTO,
    limits: PeerServiceLimits | None = None,
    clock: object | None = None,
) -> PeerMessagingService:
    return PeerMessagingService(
        repository,
        owner_pid=pid,
        owner_create_time=float(pid),
        workspace_root=f"C:/{name}",
        permission_mode="auto",
        name=name,
        inbound_policy=policy,
        limits=limits,
        clock=clock,  # type: ignore[arg-type]
    )


class _IdleApp:
    def __init__(self, *, thread_id: str | None = None, task_owned: bool = False):
        self.running = True
        self._run_task = None
        self.active_task_id = None
        self.current_thread_id = thread_id
        self.entries: list[str] = []
        tasks = (SimpleNamespace(thread_id=thread_id),) if task_owned else ()

        class Sessions:
            async def list_tasks(self, **_: object):
                return tasks

        self.sessions = Sessions()

    def _append(self, _kind: object, text: object) -> None:
        self.entries.append(str(text))

    def _request_redraw(self, **_: object) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
