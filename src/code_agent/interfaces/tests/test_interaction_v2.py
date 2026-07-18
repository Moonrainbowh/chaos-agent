from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.interfaces.diff_view import DiffController, DiffView
from code_agent.interfaces.interaction import (
    HostInteraction,
    InteractionBroker,
    InteractionPrimitive,
    InteractionResult,
    plugin_interaction,
    render_interaction,
)
from code_agent.interfaces.command_registry import REGISTRY
from code_agent.interfaces.picker import PickerItem, PickerSource, PickerState, command_picker_items
from code_agent.interfaces.steering_view import SteeringQueueView, SteeringStage
from code_agent.interfaces.tui_interactions import TuiInteractions
from code_agent.core.events import EventKind
from code_agent.plugins.models import UiPrimitive, UiRequest
from code_agent.interfaces.capability_view import ModePermissionView, PermissionSummary
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.orchestration.models import AgentMode
from code_agent.orchestration.models import AgentRole, AgentUsage, RunStatus, RunView
from code_agent.interfaces.agent_status import AgentRunStatusProjection
from code_agent.policy.models import ApprovalMode
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig


class PickerStateTests(unittest.TestCase):
    def test_keyboard_selection_completion_and_disabled_reason(self) -> None:
        picker = PickerState(
            (
                PickerItem("status", "/status", PickerSource.COMMAND, completion="/status"),
                PickerItem(
                    "plugins",
                    "/plugins",
                    PickerSource.PLUGIN,
                    enabled=False,
                    disabled_reason="plugin host unavailable",
                ),
            )
        )
        picker.update_query("plug")

        self.assertIsNone(picker.accept())
        self.assertEqual(picker.error, "plugin host unavailable")
        self.assertIn("plugin host unavailable", picker.rows(80)[0])

        picker.update_query("")
        picker.move(1)
        self.assertEqual(picker.selected.identifier, "plugins")  # type: ignore[union-attr]
        picker.move(1)
        self.assertEqual(picker.accept().completion, "/status")  # type: ignore[union-attr]

    def test_limited_window_can_reach_every_matching_command(self) -> None:
        items = tuple(
            PickerItem(f"command-{index}", f"/command-{index}", PickerSource.COMMAND)
            for index in range(12)
        )
        picker = PickerState(items, limit=4)

        for _ in range(11):
            picker.move(1)

        self.assertEqual(picker.selected.identifier, "command-11")  # type: ignore[union-attr]
        self.assertIn("12/12", picker.rows(80)[-1])
        self.assertTrue(any("command-11" in row for row in picker.rows(80)))

    def test_exact_command_completions_keep_the_selected_command_name(self) -> None:
        services = {"sessions", "history", "tasks", "evidence", "modes"}
        items = command_picker_items(REGISTRY.all(), services)

        for spec in REGISTRY.all():
            picker = PickerState(items)
            picker.update_query(spec.name)
            with self.subTest(command=spec.name):
                self.assertTrue(picker.accept().completion.startswith("/" + spec.name))  # type: ignore[union-attr]

    def test_actions_stay_disabled_when_the_parent_command_is_unavailable(self) -> None:
        mode = REGISTRY.resolve("模式")

        items = command_picker_items(REGISTRY.all(), set(), parent=mode)

        self.assertTrue(items)
        self.assertTrue(all(not item.enabled for item in items))
        self.assertTrue(all(item.disabled_reason == "requires modes" for item in items))


class SteeringQueueViewTests(unittest.TestCase):
    def test_stages_are_visible_ordered_and_never_move_backward(self) -> None:
        queue = SteeringQueueView()
        item = queue.queue("inspect tests first", "control-1")

        self.assertEqual(queue.status_line(), "queued · queue 1")
        queue.transition(item.identifier, SteeringStage.STEERED)
        queue.transition(item.identifier, SteeringStage.DEQUEUED)
        queue.transition(item.identifier, SteeringStage.APPLIED)
        self.assertEqual(queue.status_line(), "applied · queue 0")
        with self.assertRaises(ValueError):
            queue.transition(item.identifier, SteeringStage.QUEUED)

    def test_persisted_turn_and_context_events_drive_dequeued_and_applied(self) -> None:
        class App:
            def __init__(self):
                self.lines = []

            def _append(self, kind, text):
                self.lines.append(text)

        app = App()
        interactions = TuiInteractions()
        item = interactions.steering.queue("new direction", "control-1")
        interactions.steering.transition(item.identifier, SteeringStage.STEERED)

        interactions.observe_event(app, EventKind.TURN_STARTED)
        self.assertEqual(interactions.steering.items[0].stage, SteeringStage.DEQUEUED)
        interactions.observe_event(app, EventKind.CONTEXT_BUILT)
        self.assertEqual(interactions.steering.items[0].stage, SteeringStage.APPLIED)
        self.assertEqual(app.lines, ["dequeued · queue 1", "applied · queue 0"])


class AgentRunStatusProjectionTests(unittest.TestCase):
    def test_lifecycle_status_and_terminal_usage_are_visible_once(self) -> None:
        clock = [10.0]
        projection = AgentRunStatusProjection(lambda: clock[0])
        queued = RunView(
            "run-1", "parent", "review-1", AgentRole.REVIEW, AgentMode.HIGH,
            RunStatus.QUEUED, False, "Review the current changes",
        )

        self.assertIn("review · queued", projection.observe(queued))
        self.assertIsNone(projection.observe(queued))
        clock[0] = 11.0
        self.assertIn("review · running", projection.observe(replace(queued, status=RunStatus.RUNNING)))
        clock[0] = 13.0
        completed = RunView(
            queued.run_id, queued.parent_run_id, queued.agent_id, queued.role,
            queued.mode, RunStatus.COMPLETED, queued.may_write, queued.objective,
            AgentUsage(120, 3, 2),
        )
        line = projection.observe(completed)
        self.assertIn("completed", line)
        self.assertIn("3s · 120 tokens · 3 tools", line)


class HostInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_typed_result_and_cancellation_are_auditable(self) -> None:
        broker = InteractionBroker()
        token = CancellationToken()
        interaction = HostInteraction(
            "approval-1",
            InteractionPrimitive.CONFIRM,
            "Approval required",
            "Run verification?",
            source="plugin.review",
            risk="medium",
            target="run_verification",
        )
        waiting = asyncio.create_task(broker.request(interaction, token))
        queued = await broker.next_request()
        self.assertEqual(queued, interaction)
        result = InteractionResult("approval-1", True, "yes")
        self.assertTrue(broker.resolve(result))
        self.assertEqual(await waiting, result)
        self.assertIn("Enter select", render_interaction(interaction)[-1])

        cancelled_token = CancellationToken()
        cancelled = asyncio.create_task(
            broker.request(
                HostInteraction("input-1", InteractionPrimitive.INPUT, "Input", "Value"),
                cancelled_token,
            )
        )
        await broker.next_request()
        cancelled_token.cancel("task stopped")
        with self.assertRaises(CancellationError):
            await cancelled

    async def test_plugin_ui_is_converted_to_host_owned_interaction(self) -> None:
        interaction = plugin_interaction(
            UiRequest(UiPrimitive.SELECT, "Review", "Choose scope", ("Files", "Tests")),
            "review-helper",
            "plugin-ui-1",
        )

        self.assertEqual(interaction.source, "plugin.review-helper")
        self.assertEqual(interaction.primitive, InteractionPrimitive.SELECT)
        self.assertEqual(interaction.options, ("Files", "Tests"))


class DiffViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_navigates_filters_comments_and_prefers_live_diff(self) -> None:
        recorded = "--- a/old.py\n+++ b/old.py\n@@ -1 +1 @@\n-old\n+new\n"
        live = "--- a/live.py\n+++ b/live.py\n@@ -0,0 +1 @@\n+actual\n"

        class Source:
            async def read_diff(self, paths=()):
                self.paths = paths
                return live

        view = DiffView.parse(recorded)
        self.assertEqual(view.current.path, "old.py")  # type: ignore[union-attr]
        self.assertEqual(view.current.additions, 1)  # type: ignore[union-attr]
        self.assertEqual(view.current.removals, 1)  # type: ignore[union-attr]
        comment = view.comment(1, "check this hunk")
        self.assertEqual(comment.path, "old.py")
        view.filter("missing")
        self.assertIsNone(view.current)

        loaded = await DiffController(Source()).load(recorded, ("live.py",))
        self.assertEqual(loaded.current.path, "live.py")  # type: ignore[union-attr]
        self.assertEqual(loaded.render()[0].text, "live.py · +1 -0 · file 1/1")


class ModePermissionViewTests(unittest.TestCase):
    def test_mode_capability_and_permission_are_separate_lines(self) -> None:
        registry = ModeRegistry(
            standard_mode_definitions({mode: mode.value for mode in AgentMode})
        )
        profiles = {
            mode.value: ModelProfile(
                mode.value,
                ProviderConfig(
                    "https://example.test",
                    f"model-{mode.value}",
                    ApiProtocol.RESPONSES,
                    api_key_env="TEST_KEY",
                ),
                100_000,
                4_096,
            )
            for mode in AgentMode
        }
        view = ModePermissionView(
            registry.freeze(AgentMode.HIGH, profiles),
            PermissionSummary(ApprovalMode.ASK, "G:\\repo", False, True),
            applies_next_task=True,
        )

        lines = view.lines()
        self.assertIn("mode: high · model model-high", lines[0])
        self.assertIn("applies next task", lines[1])
        self.assertIn("permission: ask", lines[2])
        self.assertIn("write yes · network no", lines[3])


if __name__ == "__main__":
    unittest.main()
