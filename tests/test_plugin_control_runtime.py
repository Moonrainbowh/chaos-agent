from __future__ import annotations

import asyncio
import hashlib
import unittest

from code_agent.plugins.models import PluginContributions, PluginManifest
from code_agent.plugins.registry import ContributionSnapshot, PluginHost
from code_agent.plugins.status import PluginReloadState
from code_agent_win.plugin_runtime import PluginCommandController


def _snapshot(revision: str) -> ContributionSnapshot:
    manifest = PluginManifest(
        "review-plugin",
        "review",
        "1.0.0",
        "1",
        hashlib.sha256(revision.encode("utf-8")).hexdigest(),
        "plugin.json",
        True,
        True,
        PluginContributions(),
    )
    return ContributionSnapshot(manifests=(manifest,))


class RunTask:
    def __init__(self, done: bool) -> None:
        self.finished = done

    def done(self) -> bool:
        return self.finished


class App:
    def __init__(self, task: RunTask | None = None) -> None:
        self._run_task = task


class PluginControlRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_reload_applies_snapshot_and_refreshes_once(self) -> None:
        host = PluginHost(_snapshot("old"))
        changes: list[int] = []

        async def changed() -> None:
            await asyncio.sleep(0)
            changes.append(host.generation)

        controller = PluginCommandController(
            host,
            discover=lambda: (_snapshot("new"), ("x" * 400,)),
            on_change=changed,
        )
        controller.attach(App())

        state = await controller.reload()

        self.assertEqual(host.generation, 1)
        self.assertEqual(host.reload_state, PluginReloadState.IDLE)
        self.assertEqual(state, "Plugin reload applied")
        self.assertEqual(
            host.status("review-plugin")[0].digest,
            _snapshot("new").manifests[0].digest,
        )
        self.assertEqual(changes, [1])
        self.assertEqual(len(controller.errors), 1)
        self.assertEqual(len(controller.errors[0]), 256)

    async def test_active_reload_stages_until_settled_apply(self) -> None:
        host = PluginHost(_snapshot("old"))
        task = RunTask(False)
        changes: list[int] = []
        controller = PluginCommandController(
            host,
            discover=lambda: (_snapshot("new"), ()),
            on_change=lambda: changes.append(host.generation),
        )
        controller.attach(App(task))

        state = await controller.reload()

        self.assertEqual(state, "Plugin reload staged")
        self.assertEqual(host.generation, 0)
        self.assertEqual(host.reload_state, PluginReloadState.STAGED)
        self.assertEqual(changes, [])
        self.assertFalse(await controller.apply_staged())
        self.assertTrue(await controller.apply_staged(idle=True))
        self.assertEqual(host.generation, 1)
        self.assertEqual(host.reload_state, PluginReloadState.IDLE)
        self.assertEqual(changes, [1])

    async def test_enable_disable_advance_generation_and_refresh(self) -> None:
        host = PluginHost(_snapshot("one"))
        changes: list[int] = []
        controller = PluginCommandController(
            host, on_change=lambda: changes.append(host.generation)
        )

        disabled = await controller.disable("review-plugin")
        enabled = await controller.enable("review-plugin")

        self.assertFalse(disabled.enabled)
        self.assertTrue(enabled.enabled)
        self.assertEqual(host.generation, 2)
        self.assertEqual(changes, [1, 2])

    async def test_active_task_rejects_enable_and_disable(self) -> None:
        host = PluginHost(_snapshot("one"))
        controller = PluginCommandController(host)
        controller.attach(App(RunTask(False)))

        with self.assertRaisesRegex(RuntimeError, "only when idle"):
            await controller.disable("review-plugin")
        with self.assertRaisesRegex(RuntimeError, "only when idle"):
            await controller.enable("review-plugin")

        self.assertEqual(host.generation, 0)
        self.assertTrue(host.status("review-plugin")[0].enabled)

    async def test_discovery_failure_is_saved_without_raw_error_text(self) -> None:
        def fail():
            raise OSError("secret path and token")

        controller = PluginCommandController(
            PluginHost(_snapshot("old")), discover=fail
        )

        with self.assertRaisesRegex(RuntimeError, "plugin reload failed"):
            await controller.reload()

        self.assertEqual(controller.errors, ("discover:OSError",))


if __name__ == "__main__":
    unittest.main()
