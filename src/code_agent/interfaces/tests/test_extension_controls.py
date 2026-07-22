from __future__ import annotations

import asyncio
import unittest

from code_agent.core.cancellation import CancellationToken
from code_agent.interfaces.interaction import (
    HostInteraction,
    InteractionBroker,
    InteractionPrimitive,
    InteractionResult,
    PluginInteractionAdapter,
)
from code_agent.interfaces.picker import (
    PickerSource,
    mcp_picker_items,
    skill_picker_items,
)
from code_agent.interfaces.command_registry import CommandRegistry
from code_agent.interfaces.tui_commands import TuiCommandKind, parse_tui_command
from code_agent.interfaces.tui_workflow_commands import handle_workflow_command
from code_agent.interfaces.tui_interactions import TuiInteractions
from code_agent.plugins.events import PluginProposal
from code_agent.plugins.models import UiPrimitive, UiRequest
from code_agent.workflows.models import (
    Workflow,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowSnapshot,
)


class ExtensionControlTests(unittest.IsolatedAsyncioTestCase):
    def test_dynamic_plugin_command_uses_the_injected_registry(self) -> None:
        descriptor = type(
            "Descriptor",
            (),
            {
                "qualified_id": "reviewer.audit",
                "description": "Audit",
                "controller": "review",
                "plugin_id": "reviewer",
                "digest": "a" * 64,
                "generation": 2,
            },
        )()
        registry = CommandRegistry().with_plugin_commands((descriptor,))

        parsed = parse_tui_command(
            "/reviewer.audit src", {"plugins"}, registry
        )

        self.assertEqual(parsed.command.kind, TuiCommandKind.PLUGIN)
        self.assertEqual(parsed.command.command_name, "reviewer.audit")
        self.assertEqual(parsed.command.instruction, "src")

    def test_namespaced_plugin_mode_is_a_registered_mode_action(self) -> None:
        registry = CommandRegistry().with_plugin_modes(("review.focus",))

        parsed = parse_tui_command(
            "/模式 review.focus", {"modes"}, registry
        )

        self.assertEqual(parsed.command.kind, TuiCommandKind.MODE)
        self.assertEqual(parsed.command.instruction, "review.focus")

    async def test_workflow_command_reads_the_injected_snapshot(self) -> None:
        snapshot = WorkflowSnapshot(
            Workflow("flow", "thread", "task", "Repair"),
            (
                WorkflowNode(
                    "main",
                    "flow",
                    "main",
                    "Repair",
                    WorkflowNodeStatus.RUNNING,
                    "thread",
                ),
            ),
        )

        class Store:
            async def load_workflow_for_task(self, task_id):
                return snapshot

        class App:
            workflows = Store()
            active_task_id = "task"
            state = type("State", (), {"task_id": None})()
            rows = []

            @staticmethod
            def _columns():
                return 80

            @classmethod
            def _append(cls, kind, value):
                cls.rows.append((kind, value))

        self.assertTrue(await handle_workflow_command(App(), None))
        self.assertIn("Repair", App.rows[-1][1])

    def test_skill_and_mcp_candidates_use_shared_picker_semantics(self) -> None:
        class Skills:
            def list(self):
                return (
                    type(
                        "Skill",
                        (),
                        {
                            "identifier": "review",
                            "description": "Review",
                            "digest": "abc",
                            "sources": ("workspace",),
                        },
                    )(),
                )

        class Mcp:
            def status(self):
                return (
                    type(
                        "Server",
                        (),
                        {"name": "docs", "approved": True, "enabled": False},
                    )(),
                )

        skill = skill_picker_items(Skills(), "启用")[0]
        mcp = mcp_picker_items(Mcp(), "enable")[0]

        self.assertEqual(skill.completion, "/技能 启用 review")
        self.assertEqual(mcp.completion, "/mcp enable docs")
        self.assertEqual(
            (skill.source, mcp.source),
            (PickerSource.SKILL, PickerSource.MCP),
        )

    def test_tui_switches_to_skill_candidates_at_identifier_boundary(self) -> None:
        class Skills:
            def list(self):
                return (
                    type(
                        "Skill",
                        (),
                        {
                            "identifier": "review",
                            "description": "Review",
                            "digest": "abc",
                            "sources": (),
                        },
                    )(),
                )

        class Input:
            text = "/技能 启用 "

        class App:
            _pending_approval = None
            _pending_interaction = None
            input = Input()
            skills = Skills()

            @staticmethod
            def _columns():
                return 80

        rows = TuiInteractions().rows(App())

        self.assertIn("review", rows[0])

    async def test_plugin_adapter_displays_notify_and_brokers_answers(self) -> None:
        broker = InteractionBroker()
        shown: list[HostInteraction] = []
        adapter = PluginInteractionAdapter(broker, shown.append)
        notify = PluginProposal(
            "plugin-a",
            "a" * 64,
            1,
            "notice",
            "task_completed",
            ui=UiRequest(UiPrimitive.NOTIFY, "Done", "Finished"),
        )

        displayed = await adapter.notify(notify)

        self.assertEqual(shown, [displayed])
        confirm = PluginProposal(
            "plugin-a",
            "a" * 64,
            1,
            "confirm",
            "task_completed",
            ui=UiRequest(UiPrimitive.CONFIRM, "Confirm", "Continue?"),
        )
        waiting = asyncio.create_task(
            adapter.interact(confirm, CancellationToken())
        )
        request = await broker.next_request()
        broker.resolve(InteractionResult(request.identifier, True, "yes"))
        self.assertTrue((await waiting).accepted)

    async def test_tui_resolves_a_host_owned_plugin_selection(self) -> None:
        broker = InteractionBroker()
        interaction = HostInteraction(
            "plugin-choice",
            InteractionPrimitive.SELECT,
            "Scope",
            "Choose scope",
            ("Files", "Tests"),
            source="plugin.review",
        )
        waiting = asyncio.create_task(
            broker.request(interaction, CancellationToken())
        )
        await asyncio.sleep(0)

        class App:
            _pending_approval = None
            _pending_interaction = interaction
            interaction_broker = broker
            _interaction_done = asyncio.Event()

        controls = TuiInteractions()
        await controls.handle_key(App(), "down")
        await controls.handle_key(App(), "\r")

        result = await waiting
        self.assertTrue(result.accepted)
        self.assertEqual(result.value, "Tests")


if __name__ == "__main__":
    unittest.main()
