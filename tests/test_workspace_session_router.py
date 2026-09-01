from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from code_agent.core._json import JSONValue
from code_agent.sessions.rewind_repository import RewindSessionRepository
from code_agent_win.rewind_sessions import CoordinatedSessionRepository
from code_agent_win.workspace_session_router import WorkspaceSessionRouter


class _RecordingCoordinated(CoordinatedSessionRepository):
    def __init__(
        self,
        base: RewindSessionRepository,
        root: Path,
        fingerprint: str,
        calls: list[tuple[Path, str, str | None, str]],
        *,
        owner: str | None = None,
    ) -> None:
        self._base = base
        self._root = root
        self._workspace_fingerprint = fingerprint
        self._calls = calls
        self._owner = owner

    def for_owner(self, owner_thread_id: str) -> "_RecordingCoordinated":
        return _RecordingCoordinated(
            self._base,
            self._root,
            self._workspace_fingerprint,
            self._calls,
            owner=owner_thread_id,
        )

    async def create_checkpoint(
        self,
        thread_id: str,
        label: str,
        metadata: Mapping[str, JSONValue] | None = None,
    ) -> str:
        del label, metadata
        self._calls.append(
            (
                self._root,
                self._workspace_fingerprint,
                self._owner,
                thread_id,
            )
        )
        return f"checkpoint:{thread_id}"


class WorkspaceSessionRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source = (root / "source").resolve()
        self.task = (root / "task").resolve()
        self.source.mkdir()
        self.task.mkdir()
        self.base = RewindSessionRepository(root / "sessions.sqlite3")
        self.calls: list[tuple[Path, str, str | None, str]] = []
        self.thread_roots: dict[str, Path] = {}
        fingerprints = {
            self.source: "1" * 64,
            self.task: "2" * 64,
        }
        self.facades = {
            path: _RecordingCoordinated(
                self.base, path, fingerprint, self.calls
            )
            for path, fingerprint in fingerprints.items()
        }
        self.router = WorkspaceSessionRouter(
            self.base,
            self.source,
            self.thread_roots.get,
            self.facades.__getitem__,
        )

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def test_source_and_task_checkpoints_use_isolated_facades(self) -> None:
        self.thread_roots["task-thread"] = self.task

        source_id = await self.router.create_checkpoint(
            "source-thread", "source", {"scope": "source"}
        )
        task_id = await self.router.create_checkpoint(
            "task-thread", "task", {"scope": "task"}
        )

        self.assertEqual(source_id, "checkpoint:source-thread")
        self.assertEqual(task_id, "checkpoint:task-thread")
        self.assertEqual(
            self.calls,
            [
                (self.source, "1" * 64, None, "source-thread"),
                (self.task, "2" * 64, None, "task-thread"),
            ],
        )

    async def test_unbound_child_falls_back_to_owner_workspace(self) -> None:
        self.thread_roots["owner-thread"] = self.task

        checkpoint = await self.router.for_owner(
            "owner-thread"
        ).create_checkpoint("child-thread", "child")

        self.assertEqual(checkpoint, "checkpoint:child-thread")
        self.assertEqual(
            self.calls,
            [(self.task, "2" * 64, "owner-thread", "child-thread")],
        )

    async def test_bound_child_uses_child_workspace_and_keeps_owner_anchor(self) -> None:
        self.thread_roots["owner-thread"] = self.source
        self.thread_roots["child-thread"] = self.task

        await self.router.for_owner("owner-thread").create_checkpoint(
            "child-thread", "child"
        )

        self.assertEqual(
            self.calls,
            [(self.task, "2" * 64, "owner-thread", "child-thread")],
        )

    async def test_other_repository_methods_are_forwarded_to_unique_base(self) -> None:
        thread_id = await self.router.create_thread()

        self.assertEqual(
            await self.base.load_thread_relation(thread_id),
            await self.router.load_thread_relation(thread_id),
        )

    async def test_constructor_and_callbacks_fail_closed(self) -> None:
        cases = (
            ((object(), self.source, self.thread_roots.get, self.facades.__getitem__), TypeError),
            ((self.base, str(self.source), self.thread_roots.get, self.facades.__getitem__), TypeError),
            ((self.base, self.source, None, self.facades.__getitem__), TypeError),
            ((self.base, self.source, self.thread_roots.get, None), TypeError),
        )
        for arguments, error_type in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(error_type):
                    WorkspaceSessionRouter(*arguments)

        wrong_root = WorkspaceSessionRouter(
            self.base, self.source, lambda _: "not-a-path", self.facades.__getitem__
        )
        with self.assertRaises(TypeError):
            await wrong_root.create_checkpoint("thread", "label")

        wrong_facade = WorkspaceSessionRouter(
            self.base, self.source, lambda _: self.task, lambda _: object()
        )
        with self.assertRaises(TypeError):
            await wrong_facade.create_checkpoint("thread", "label")

    async def test_rejects_foreign_base_and_blank_identifiers(self) -> None:
        foreign = RewindSessionRepository(
            Path(self.temporary.name) / "foreign.sqlite3"
        )
        try:
            facade = _RecordingCoordinated(
                foreign, self.task, "3" * 64, self.calls
            )
            router = WorkspaceSessionRouter(
                self.base, self.source, lambda _: self.task, lambda _: facade
            )
            with self.assertRaises(RuntimeError):
                await router.create_checkpoint("thread", "label")
        finally:
            del foreign

        for value in (None, 1, "", "   "):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    self.router.for_owner(value)  # type: ignore[arg-type]
                with self.assertRaises((TypeError, ValueError)):
                    await self.router.create_checkpoint(value, "label")  # type: ignore[arg-type]

    async def test_invalid_metadata_is_rejected_before_facade_side_effects(self) -> None:
        invalid_values = (
            {1: "non-string-key"},
            {"value": object()},
            {"value": float("nan")},
        )

        for metadata in invalid_values:
            with self.subTest(metadata=metadata):
                with self.assertRaises((TypeError, ValueError)):
                    await self.router.create_checkpoint(
                        "thread", "label", metadata  # type: ignore[arg-type]
                    )
                self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
