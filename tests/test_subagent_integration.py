from __future__ import annotations

import unittest

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import (
    ActionRequest,
    ActionResult,
    Message,
    ModelEvent,
    ModelEventKind,
    Usage,
)
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentMode,
    AgentRole,
    AgentUsage,
    ChildRunRequest,
    ChildRunResult,
    RunStatus,
)
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent_win.agent_modes import default_child_mode
from code_agent_win.subagents import (
    EngineChildRunner,
    RestrictedDispatcher,
    SubagentRuntime,
    SubagentTool,
)


def _runtime():
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
    tools = {
        mode: ("read_file", "search_text", "write_file", "delegate_agent")
        for mode in AgentMode
    }
    registry = ModeRegistry(
        standard_mode_definitions(
            {mode: mode.value for mode in AgentMode}, tools_by_mode=tools
        )
    )
    return registry, profiles


class RestrictedDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_restricted_dispatcher_forwards_execution_context_by_keyword(
        self,
    ) -> None:
        class Inner:
            def tools(self):
                return ()

            async def dispatch(
                self,
                request,
                cancellation,
                task_authorization=None,
                *,
                execution_context=None,
            ):
                self.context = execution_context
                return ActionResult(request.id, request.name, {})

        inner = Inner()
        restricted = RestrictedDispatcher(inner, ("read_file",))
        context = ActionExecutionContext("owner", "origin", "call-1")

        result = await restricted.dispatch(
            ActionRequest("call-1", "read_file", {"path": "note.txt"}),
            CancellationToken(),
            execution_context=context,
        )

        self.assertFalse(result.is_error)
        self.assertIs(inner.context, context)


class EngineChildRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_publishes_live_child_statuses(self) -> None:
        registry, profiles = _runtime()

        class Engine:
            async def run(self, objective, cancellation=None):
                yield AgentEvent(
                    EventKind.MESSAGE_ADDED,
                    {"message": Message(role="assistant", content="done").to_dict()},
                )

        runtime = SubagentRuntime(
            EngineChildRunner(lambda _agent, _parent: (Engine(), None)),
            registry,
            profiles,
        )
        statuses = []
        runtime.subscribe(lambda view: statuses.append(view.status))
        token = runtime.activate("parent-live")
        try:
            await runtime.dispatch(
                ActionRequest(
                    "live-1", "delegate_agent", {"objective": "review", "role": "review"}
                ),
                CancellationToken(),
            )
        finally:
            await runtime.release("parent-live")
            runtime.reset(token)

        self.assertEqual(
            statuses,
            [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.COMPLETED],
        )

    async def test_child_events_become_bounded_advisory_result_and_usage(self) -> None:
        registry, profiles = _runtime()

        class Engine:
            async def run(self, objective, cancellation=None):
                yield AgentEvent(EventKind.RUN_STARTED, {"thread_id": "child-thread"})
                yield AgentEvent(EventKind.ACTION_REQUESTED, {"request": {"id": "tool-1", "name": "read_file", "arguments": {"path": "x"}}})
                yield AgentEvent(EventKind.MODEL_EVENT, {"event": ModelEvent(ModelEventKind.USAGE, usage=Usage(12, 3)).to_dict()})
                yield AgentEvent(EventKind.MESSAGE_ADDED, {"message": Message(role="assistant", content="review finding").to_dict()})

        agent = AgentDefinition(
            "reviewer",
            AgentRole.REVIEW,
            registry.freeze("medium", profiles),
            "Review the bounded objective.",
            ("read_file",),
        )
        request = ChildRunRequest("parent", "review", agent, 1, 100, 4, 30, "child-1")

        result = await EngineChildRunner(
            lambda _agent, _parent: (Engine(), None)
        ).run(
            request, CancellationToken()
        )

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.summary, "review finding")
        self.assertEqual(result.usage.total_tokens, 15)
        self.assertEqual(result.usage.tool_calls, 1)
        self.assertEqual(result.references[0].identifier, "child-thread")

    async def test_review_role_is_read_only_and_result_is_marked_advisory(self) -> None:
        registry, profiles = _runtime()

        class Supervisor:
            async def run(self, request):
                self.request = request
                return ChildRunResult(
                    request.run_id,
                    RunStatus.COMPLETED,
                    "looks correct",
                    AgentUsage(10, 1, 1),
                )

        supervisor = Supervisor()
        tool = SubagentTool(supervisor, registry, profiles)  # type: ignore[arg-type]
        result = await tool.dispatch(
            ActionRequest(
                "delegate-1",
                "delegate_agent",
                {"objective": "review changes", "role": "review"},
            ),
            CancellationToken(),
        )

        self.assertFalse(result.is_error)
        self.assertTrue(result.output["advisory"])
        self.assertFalse(supervisor.request.agent.may_write)
        self.assertNotIn("write_file", supervisor.request.agent.effective_tools)
        self.assertNotIn("delegate_agent", supervisor.request.agent.effective_tools)

    async def test_child_count_budget_resets_for_a_different_parent_task(self) -> None:
        registry, profiles = _runtime()

        class Engine:
            async def run(self, objective, cancellation=None):
                yield AgentEvent(
                    EventKind.MESSAGE_ADDED,
                    {"message": Message(role="assistant", content="done").to_dict()},
                )

        runtime = SubagentRuntime(
            EngineChildRunner(lambda _agent, _parent: (Engine(), None)),
            registry,
            profiles,
        )
        first_token = runtime.activate("parent-1")
        for index in range(8):
            result = await runtime.dispatch(
                ActionRequest(
                    f"first-{index}",
                    "delegate_agent",
                    {"objective": "inspect", "role": "review", "token_budget": 256},
                ),
                CancellationToken(),
            )
            self.assertFalse(result.is_error)
        await runtime.release("parent-1")
        runtime.reset(first_token)

        second_token = runtime.activate("parent-2")
        result = await runtime.dispatch(
            ActionRequest(
                "second-1",
                "delegate_agent",
                {"objective": "inspect", "role": "review", "token_budget": 256},
            ),
            CancellationToken(),
        )
        await runtime.release("parent-2")
        runtime.reset(second_token)

        self.assertFalse(result.is_error)
        await runtime.aclose()

    async def test_parent_mode_defaults_select_requested_child_profile(self) -> None:
        registry, profiles = _runtime()

        class Supervisor:
            async def run(self, request):
                self.request = request
                return ChildRunResult(
                    request.run_id, RunStatus.COMPLETED, "done", AgentUsage()
                )

        cases = {
            AgentMode.MEDIUM: AgentMode.LOW,
            AgentMode.HIGH: AgentMode.LOW,
            AgentMode.ULTRA: AgentMode.MEDIUM,
        }
        for parent, expected_child in cases.items():
            with self.subTest(parent=parent.value):
                supervisor = Supervisor()
                tool = SubagentTool(
                    supervisor, registry, profiles, default_child_mode(parent)
                )  # type: ignore[arg-type]
                await tool.dispatch(
                    ActionRequest(
                        f"child-{parent.value}",
                        "delegate_agent",
                        {"objective": "review", "role": "review"},
                    ),
                    CancellationToken(),
                )
                self.assertEqual(
                    supervisor.request.agent.mode.definition.mode, expected_child
                )


if __name__ == "__main__":
    unittest.main()
