from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from chaos_agent.remote.applications import RemoteApplications
from chaos_agent.runtime_selection_control import RuntimeSelectionSummary


class _RuntimeSelection:
    """Preserve the target mode just as the public runtime control does."""

    def __init__(self, calls):
        self.calls = calls
        self.current = RuntimeSelectionSummary(
            "single", "profile-a", "model-a", "responses", "medium", 2048, "low",
        )

    def profiles(self):
        return (("profile-a", "model-a", "responses"), ("profile-b", "model-b", "responses"))

    async def use(self, *, profile, topology, reasoning_effort, idle):
        self.calls.append(("runtime", profile, topology, reasoning_effort, idle))
        self.current = replace(
            self.current, profile=profile, topology=topology,
            reasoning_effort=reasoning_effort,
        )


class _ModeControl:
    def __init__(self, runtime, calls):
        self.runtime, self.calls = runtime, calls
        self.error = None

    async def use(self, name, *, idle):
        self.calls.append(("mode", name, idle))
        if self.error is not None:
            raise self.error
        self.runtime.current = replace(self.runtime.current, legacy_mode=name)


class _Application:
    def __init__(self, root):
        self.workspace_root = root
        self.calls = []
        self.runtime_selection = _RuntimeSelection(self.calls)
        self.tui = SimpleNamespace(modes=_ModeControl(self.runtime_selection, self.calls))
        self.started = False
        self.closed = False

    async def startup(self):
        self.started = True

    async def aclose(self):
        self.closed = True


class RemoteApplicationsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.primary = _Application(self.root / "primary")
        self.child = _Application(self.root / "child")
        self.factory_roots = []

        def factory(root):
            self.factory_roots.append(root)
            return self.child

        self.applications = RemoteApplications(self.primary, factory)

    async def test_reused_child_updates_mode_before_runtime_selection(self):
        first = await self.applications.for_root(self.child.workspace_root)
        self.assertIs(first, self.child)
        self.assertTrue(self.child.started)
        self.child.calls.clear()
        self.primary.runtime_selection.current = replace(
            self.primary.runtime_selection.current, legacy_mode="high",
            profile="profile-b", topology="hybrid", reasoning_effort="high",
        )

        second = await self.applications.for_root(self.child.workspace_root)

        self.assertIs(second, first)
        self.assertEqual(self.factory_roots, [self.child.workspace_root])
        self.assertFalse(self.child.closed)
        self.assertEqual(self.child.calls, [
            ("mode", "high", True),
            ("runtime", "profile-b", "hybrid", "high", True),
        ])
        selected = self.child.runtime_selection.current
        self.assertEqual((selected.legacy_mode, selected.profile, selected.topology, selected.reasoning_effort),
                         ("high", "profile-b", "hybrid", "high"))

    async def test_restored_primary_selection_controls_reuse_even_if_mode_ui_is_stale(self):
        await self.applications.for_root(self.child.workspace_root)
        self.child.calls.clear()
        # Restoring a task updates the actual runtime snapshot, independently of
        # the mode picker's projection. Its runtime summary is authoritative.
        self.primary.tui.modes.current = SimpleNamespace(name="low")
        self.primary.runtime_selection.current = replace(
            self.primary.runtime_selection.current, legacy_mode="ultra", reasoning_effort="xhigh",
        )

        await self.applications.for_root(self.child.workspace_root)

        self.assertEqual(self.child.calls[0], ("mode", "ultra", True))
        self.assertEqual(self.child.runtime_selection.current.legacy_mode, "ultra")
        self.assertEqual(self.child.runtime_selection.current.reasoning_effort, "xhigh")

    async def test_matching_mode_does_not_require_or_call_mode_control(self):
        del self.child.tui

        await self.applications.for_root(self.child.workspace_root)

        self.assertEqual(self.child.calls, [("runtime", "profile-a", "single", "medium", True)])

    async def test_missing_mode_interface_fails_before_runtime_switch_on_reuse(self):
        await self.applications.for_root(self.child.workspace_root)
        self.child.calls.clear()
        self.primary.runtime_selection.current = replace(
            self.primary.runtime_selection.current, legacy_mode="high",
        )
        for broken in (None, SimpleNamespace(), SimpleNamespace(modes=SimpleNamespace(use=None))):
            with self.subTest(tui=broken):
                self.child.tui = broken
                with self.assertRaisesRegex(RuntimeError, "mode is unavailable"):
                    await self.applications.for_root(self.child.workspace_root)
                self.assertEqual(self.child.calls, [])
                self.assertEqual(self.child.runtime_selection.current.legacy_mode, "low")
                self.assertIs(self.applications.child, self.child)

    async def test_mode_switch_failure_does_not_switch_runtime(self):
        await self.applications.for_root(self.child.workspace_root)
        self.child.calls.clear()
        self.primary.runtime_selection.current = replace(
            self.primary.runtime_selection.current, legacy_mode="high",
        )
        self.child.tui.modes.error = RuntimeError("mode rejected")

        with self.assertRaisesRegex(RuntimeError, "mode rejected"):
            await self.applications.for_root(self.child.workspace_root)

        self.assertEqual(self.child.calls, [("mode", "high", True)])
        self.assertEqual(self.child.runtime_selection.current.legacy_mode, "low")

    async def test_primary_root_reuses_primary_without_switching(self):
        self.assertIs(await self.applications.for_root(self.primary.workspace_root), self.primary)
        self.assertEqual(self.factory_roots, [])
        self.assertEqual(self.primary.calls, [])

    async def test_application_without_runtime_selection_keeps_compatibility(self):
        del self.primary.runtime_selection

        self.assertIs(await self.applications.for_root(self.child.workspace_root), self.child)

        self.assertEqual(self.child.calls, [])

    async def test_default_factory_passes_primary_current_mode_at_initial_creation(self):
        applications = RemoteApplications(self.primary)
        self.primary.runtime_selection.current = replace(
            self.primary.runtime_selection.current, legacy_mode="high",
        )
        with patch("chaos_agent.remote.applications._create_application", return_value=self.child) as create:
            self.assertIs(applications._create_for_root(self.child.workspace_root), self.child)
            create.assert_called_once_with(self.child.workspace_root, "high")


if __name__ == "__main__":
    unittest.main()
