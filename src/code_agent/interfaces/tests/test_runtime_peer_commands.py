from __future__ import annotations

import unittest
from types import SimpleNamespace

from code_agent.interfaces.controller import AgentController
from code_agent.interfaces.terminal_state import ApprovalBroker
from code_agent.interfaces.tests._support import FakeEngine
from code_agent.interfaces.windows_tui import WindowsTerminalApp


class RuntimeSelection:
    def __init__(self) -> None:
        self.current = SimpleNamespace(
            topology="single",
            profile="gpt56_sol",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
        )
        self.calls: list[dict[str, object]] = []

    @staticmethod
    def profiles() -> tuple[tuple[str, str, str], ...]:
        return (
            ("gpt56_sol", "gpt-5.6-sol", "chat_completions"),
            ("gpt56_terra", "gpt-5.6-terra", "chat_completions"),
            ("gpt56_luna", "gpt-5.6-luna", "chat_completions"),
        )

    async def use(self, **values: object) -> object:
        self.calls.append(values)
        return self.current


class PeerControl:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def list_agents(self) -> tuple[object, ...]:
        self.calls.append(("list_agents",))
        return (
            SimpleNamespace(
                session_ref="peer-a",
                name="worker",
                status=SimpleNamespace(value="idle"),
                inbound_policy=SimpleNamespace(value="auto"),
            ),
        )

    async def rename(self, name: str) -> object:
        self.calls.append(("rename", name))
        return SimpleNamespace(name=name, session_ref="self-ref")

    async def send_message(self, target: str, text: str) -> object:
        self.calls.append(("send_message", target, text))
        return SimpleNamespace(
            message=SimpleNamespace(
                id="message-1", status=SimpleNamespace(value="queued")
            )
        )

    async def set_inbound_policy(self, policy: str) -> object:
        self.calls.append(("set_inbound_policy", policy))
        return SimpleNamespace(inbound_policy=SimpleNamespace(value=policy))

    async def list_inbox(self) -> tuple[object, ...]:
        self.calls.append(("list_inbox",))
        return (
            SimpleNamespace(
                id="message-1",
                status=SimpleNamespace(value="held"),
                content="review the registry",
            ),
        )

    async def resolve_held(self, message_id: str, *, accept: bool) -> object:
        self.calls.append(("resolve_held", message_id, accept))
        return SimpleNamespace(
            id=message_id,
            status=SimpleNamespace(value="queued" if accept else "refused"),
        )


def app(**controls: object) -> WindowsTerminalApp:
    return WindowsTerminalApp(
        AgentController(FakeEngine(())),
        ApprovalBroker(),
        write=lambda _: None,
        **controls,
    )


class RuntimeSelectionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_runtime_axis_dispatches_independently(self) -> None:
        runtime = RuntimeSelection()
        value = app(runtime_selection=runtime)

        for command in (
            "/模式 代理 team",
            "/模式 模型 terra",
            "/模式 思考 max",
        ):
            self.assertTrue(await value.submit(command))

        self.assertEqual(
            runtime.calls,
            [
                {"topology": "team", "idle": True},
                {"profile": "gpt56_terra", "idle": True},
                {"reasoning_effort": "max", "idle": True},
            ],
        )
        self.assertEqual(value._current_model(), "gpt-5.6-sol")

    async def test_model_short_name_must_match_one_unique_profile_suffix(self) -> None:
        runtime = RuntimeSelection()
        runtime.profiles = lambda: (
            ("first_sol", "a", "chat_completions"),
            ("second_sol", "b", "chat_completions"),
        )
        value = app(runtime_selection=runtime)

        self.assertFalse(await value.submit("/模式 模型 sol"))
        self.assertEqual(runtime.calls, [])
        self.assertIn("ambiguous", value.state.entries[-1].text)

    async def test_legacy_mode_still_works_without_runtime_selection(self) -> None:
        class Modes:
            current = SimpleNamespace(name="medium", model="old-model")

            @staticmethod
            def list() -> tuple[object, ...]:
                return (Modes.current,)

            async def use(self, name: str, *, idle: bool) -> object:
                self.seen = (name, idle)
                return SimpleNamespace(name=name, model="old-model")

        modes = Modes()
        value = app(modes=modes)

        self.assertTrue(await value.submit("/模式 high"))
        self.assertEqual(modes.seen, ("high", True))

    async def test_legacy_mode_confirmation_uses_effective_runtime_axes(self) -> None:
        class Modes:
            current = SimpleNamespace(name="medium", model="mode-default-sol")

            @staticmethod
            def list() -> tuple[object, ...]:
                return (Modes.current,)

            async def use(self, name: str, *, idle: bool) -> object:
                return SimpleNamespace(name=name, model="mode-default-sol")

        runtime = RuntimeSelection()
        runtime.current = SimpleNamespace(
            topology="team",
            profile="gpt56_terra",
            model="gpt-5.6-terra",
            reasoning_effort="max",
        )
        value = app(modes=Modes(), runtime_selection=runtime)

        self.assertTrue(await value.submit("/模式 high"))
        message = value.state.entries[-1].text
        self.assertIn("high · team · gpt56_terra · gpt-5.6-terra · max", message)
        self.assertNotIn("mode-default-sol", message)


class PeerCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_peer_actions_and_compatibility_aliases_dispatch(self) -> None:
        peers = PeerControl()
        value = app(peers=peers)

        for command in (
            "/list-agents",
            "/peers",
            "/rename worker one",
            "/会话 发送 peer-a concise handoff",
            "/会话 接收 hold",
            "/会话 待处理",
            "/会话 接受 message-1",
            "/会话 拒绝 message-2",
        ):
            self.assertTrue(await value.submit(command), command)

        self.assertEqual(
            peers.calls,
            [
                ("list_agents",),
                ("list_agents",),
                ("rename", "worker one"),
                ("send_message", "peer-a", "concise handoff"),
                ("set_inbound_policy", "hold"),
                ("list_inbox",),
                ("resolve_held", "message-1", True),
                ("resolve_held", "message-2", False),
            ],
        )

    async def test_history_remains_available_without_peer_control(self) -> None:
        class Sessions:
            async def list_threads(self) -> tuple[object, ...]:
                return (SimpleNamespace(id="thread-1"),)

        value = app(sessions=Sessions())

        self.assertTrue(await value.submit("/会话 历史"))
        self.assertIn("thread-1", value.state.entries[-1].text)
        self.assertFalse(await value.submit("/会话 在线"))

    async def test_send_preserves_windows_path_and_quoted_peer_name(self) -> None:
        peers = PeerControl()
        value = app(peers=peers)

        self.assertTrue(
            await value.submit(r'/会话 发送 "worker one" inspect F:\code\file.py')
        )

        self.assertEqual(
            peers.calls,
            [("send_message", "worker one", r"inspect F:\code\file.py")],
        )


if __name__ == "__main__":
    unittest.main()
