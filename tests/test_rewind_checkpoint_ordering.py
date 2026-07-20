from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from code_agent_win.rewind_sessions import CoordinatedSessionRepository


class _Lease:
    def __init__(self, gate: "_Gate") -> None:
        self.gate = gate

    async def release(self) -> None:
        self.gate.events.append("gate.release")
        self.gate.lock.release()


class _Gate:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.lock = asyncio.Lock()

    async def acquire(self) -> _Lease:
        await self.lock.acquire()
        self.events.append("gate.acquire")
        return _Lease(self)


class _Sessions:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.high_water = 0
        self.prepared = False
        self.block_step: str | None = None
        self.operation_started = asyncio.Event()
        self.operation_continue = asyncio.Event()

    async def _block(self, step: str) -> None:
        if self.block_step == step:
            self.operation_started.set()
            await self.operation_continue.wait()

    async def ensure_rewind_coverage(self, fingerprint: str) -> object:
        self.events.append("sessions.ensure")
        await self._block("ensure")
        return SimpleNamespace(token=fingerprint)

    async def get_rewind_checkpoint_anchor(
        self, token: object, owner: str
    ) -> object:
        self.events.append(f"sessions.anchor:{owner}:{self.high_water}")
        await self._block("anchor")
        if self.prepared:
            raise RuntimeError("prepared mutation")
        return SimpleNamespace(high_water=self.high_water)

    async def create_checkpoint(
        self, thread: str, label: str, metadata=None, *, rewind_anchor=None
    ) -> str:
        self.events.append(f"sessions.create:{rewind_anchor.high_water}")
        await self._block("create")
        return "checkpoint"

    async def legacy(self, value: str) -> str:
        return f"legacy:{value}"


def _facade() -> tuple[CoordinatedSessionRepository, _Sessions, _Gate]:
    gate = _Gate()
    sessions = _Sessions(gate.events)
    facade = CoordinatedSessionRepository(sessions, gate, "a" * 64)
    return facade, sessions, gate


async def _cancel_during_ordered_call(step: str) -> list[str]:
    facade, sessions, gate = _facade()
    sessions.block_step = step
    task = asyncio.create_task(facade.create_checkpoint("thread", "cancel"))
    await sessions.operation_started.wait()
    task.cancel()
    contender = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0)
    if contender.done():
        raise AssertionError("gate released before ordered call settled")
    sessions.operation_continue.set()
    with unittest.TestCase().assertRaises(asyncio.CancelledError):
        await task
    lease = await contender
    await lease.release()
    return gate.events


class CheckpointOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_checkpoint_commits_before_mutation(self) -> None:
        facade, _, gate = _facade()

        await facade.create_checkpoint("thread", "before")
        lease = await gate.acquire()
        gate.events.extend(("mutation.prepare", "file.write", "mutation.complete"))
        await lease.release()

        self.assertLess(
            gate.events.index("sessions.create:0"),
            gate.events.index("mutation.prepare"),
        )

    async def test_checkpoint_commits_after_completed_mutation(self) -> None:
        facade, sessions, gate = _facade()
        lease = await gate.acquire()
        gate.events.extend(("mutation.prepare", "file.write", "mutation.complete"))
        sessions.high_water = 1
        await lease.release()

        await facade.create_checkpoint("thread", "after")

        self.assertLess(
            gate.events.index("mutation.complete"),
            gate.events.index("sessions.create:1"),
        )

    async def test_checkpoint_cannot_commit_old_high_water_after_file_write(self) -> None:
        facade, sessions, gate = _facade()
        lease = await gate.acquire()
        gate.events.extend(("mutation.prepare", "file.write"))
        sessions.high_water = 4
        pending = asyncio.create_task(facade.create_checkpoint("thread", "after"))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        gate.events.append("mutation.complete")
        await lease.release()

        await pending
        self.assertIn("sessions.create:4", gate.events)

    async def test_prepared_mutation_blocks_checkpoint(self) -> None:
        facade, sessions, _ = _facade()
        sessions.prepared = True

        with self.assertRaisesRegex(RuntimeError, "prepared mutation"):
            await facade.create_checkpoint("thread", "blocked")

    async def test_cancelled_checkpoint_commit_settles_before_gate_release(self) -> None:
        for step in ("ensure", "anchor", "create"):
            with self.subTest(step=step):
                events = await _cancel_during_ordered_call(step)
                self.assertLess(
                    events.index(f"sessions.{step}" if step == "ensure" else (
                        "sessions.anchor:thread:0"
                        if step == "anchor" else "sessions.create:0"
                    )),
                    events.index("gate.release"),
                )

    async def test_gate_or_anchor_failure_never_falls_back_unanchored(self) -> None:
        facade, sessions, gate = _facade()
        sessions.prepared = True

        with self.assertRaises(RuntimeError):
            await facade.create_checkpoint("thread", "blocked")

        self.assertFalse(any(event.startswith("sessions.create") for event in gate.events))

    async def test_child_facade_anchors_checkpoint_to_root_owner(self) -> None:
        facade, _, gate = _facade()

        await facade.for_owner("root-owner").create_checkpoint("child", "child")

        self.assertIn("sessions.anchor:root-owner:0", gate.events)

    async def test_for_owner_rejects_blank_owner(self) -> None:
        facade, _, _ = _facade()

        for owner in ("", "   "):
            with self.subTest(owner=owner):
                with self.assertRaises(ValueError):
                    facade.for_owner(owner)

    async def test_forwarded_repository_methods_preserve_legacy_behavior(self) -> None:
        facade, _, _ = _facade()

        self.assertEqual(await facade.legacy("value"), "legacy:value")


if __name__ == "__main__":
    unittest.main()
