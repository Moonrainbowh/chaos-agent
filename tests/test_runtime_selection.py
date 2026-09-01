from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.config.loader import RuntimeConfig
from code_agent.orchestration.models import AgentMode
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.policy.models import ApprovalMode
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent_win.app import create_application
from code_agent_win.runtime_controls import RuntimeSelectionControl


def _profiles() -> dict[str, ModelProfile]:
    return {
        name: ModelProfile(
            name,
            ProviderConfig(
                "https://api.example.test",
                f"gpt-5.6-{name}",
                ApiProtocol.CHAT_COMPLETIONS,
                api_key_env="TEST_KEY",
            ),
            200_000,
            8_000 + index,
        )
        for index, name in enumerate(("sol", "terra", "luna"))
    }


class RuntimeSelectionControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_switches_are_independent_and_failure_is_atomic(self) -> None:
        profiles = _profiles()
        registry = ModeRegistry(
            standard_mode_definitions(
                {mode: "sol" for mode in AgentMode},
                tools_by_mode={
                    mode: ("read_file", "delegate_agent") for mode in AgentMode
                },
            )
        )
        initial = registry.freeze_runtime(
            "medium",
            profiles,
            profile_id="sol",
            topology="single",
            reasoning_effort="medium",
        )
        applied = []
        fail = False

        async def apply(snapshot):
            if fail:
                raise RuntimeError("rebuild failed")
            applied.append(snapshot)

        control = RuntimeSelectionControl(profiles, initial, apply)

        self.assertEqual(
            tuple(item[0] for item in control.list_profiles()),
            ("sol", "terra", "luna"),
        )
        self.assertEqual(control.list_topologies(), ("single", "team"))
        self.assertEqual(
            control.list_reasoning_efforts(),
            ("low", "medium", "high", "xhigh", "max"),
        )

        selected = await control.use(
            topology="team", reasoning_effort="max", idle=True
        )
        self.assertEqual(selected.topology, "team")
        self.assertEqual(selected.profile, "sol")
        self.assertEqual(selected.reasoning_effort, "max")

        selected = await control.use(profile="terra", idle=True)
        self.assertEqual(selected.profile, "terra")
        self.assertEqual(selected.topology, "team")
        self.assertEqual(selected.reasoning_effort, "max")
        stable = control.snapshot

        fail = True
        with self.assertRaisesRegex(RuntimeError, "rebuild failed"):
            await control.use(profile="luna", idle=True)

        self.assertIs(control.snapshot, stable)
        self.assertEqual(control.current.profile, "terra")
        self.assertEqual(len(applied), 2)

    async def test_active_task_and_unknown_profile_leave_selection_unchanged(self) -> None:
        profiles = _profiles()
        registry = ModeRegistry(
            standard_mode_definitions({mode: "sol" for mode in AgentMode})
        )
        initial = registry.freeze_runtime(
            "medium",
            profiles,
            profile_id="sol",
            topology="single",
            reasoning_effort="low",
        )

        async def apply(snapshot):
            raise AssertionError("apply must not run")

        control = RuntimeSelectionControl(profiles, initial, apply)
        with self.assertRaisesRegex(RuntimeError, "only when idle"):
            await control.use(topology="team", idle=False)
        with self.assertRaisesRegex(ValueError, "not configured"):
            await control.use(profile="missing", idle=True)

        self.assertEqual(control.current.profile, "sol")
        self.assertEqual(control.current.topology, "single")


class RuntimeSelectionApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_application_switch_rebuilds_profile_and_team_dispatcher(self) -> None:
        profiles = _profiles()
        runtime = RuntimeConfig(
            provider=profiles["sol"].provider,
            profile="sol",
            approval_mode=ApprovalMode.AUTO,
            allow_sensitive_paths=False,
            config_path=Path("missing.toml"),
            profiles=tuple(profiles.values()),
        )
        with tempfile.TemporaryDirectory() as temporary:
            container = Path(temporary).resolve()
            root = container / "workspace"
            root.mkdir()
            state = container / "state"
            state.mkdir()
            with patch(
                "code_agent_win.app._model_client", side_effect=lambda _: object()
            ), patch(
                "code_agent_win.app._session_path",
                return_value=state / "sessions.sqlite3",
            ), patch(
                "code_agent_win.app._product_state_root", return_value=state
            ), patch(
                "code_agent_win.app._workspace_storage_path",
                return_value=container / "managed-workspaces",
            ), patch(
                "code_agent_win.app.load_runtime_config", return_value=runtime
            ):
                application = create_application(root)

            original_runner = application.controller._engine
            self.assertIs(
                application.tui.runtime_selection,
                application.runtime_selection,
            )
            selected = await application.runtime_selection.use(
                topology="team",
                profile="terra",
                reasoning_effort="max",
                idle=True,
            )

            self.assertEqual(selected.profile, "terra")
            self.assertEqual(application.model.current.profile.name, "terra")
            self.assertEqual(application.mode.profile_id, "terra")
            self.assertEqual(application.mode.effective_reasoning_effort, "max")
            self.assertIsNot(application.controller._engine, original_runner)
            self.assertIn(
                "delegate_agent",
                tuple(
                    tool.name
                    for tool in application.controller._engine._actions.tools()
                ),
            )

            await application.tui.modes.use("high", idle=True)
            self.assertEqual(application.mode.definition.mode.value, "high")
            self.assertEqual(application.mode.profile_id, "terra")
            self.assertEqual(application.mode.topology.value, "team")
            self.assertEqual(application.mode.effective_reasoning_effort, "max")

            task = await application.foreground_tasks.start("freeze runtime")
            self.assertEqual(task.contract.profile_id, "terra")
            self.assertEqual(task.contract.agent_topology, "team")
            self.assertEqual(task.contract.reasoning_effort, "max")
            self.assertEqual(task.contract.runtime_mode, "high")
            self.assertEqual(
                task.contract.runtime_selection_digest,
                application.mode.digest,
            )

            await application.runtime_selection.use(
                topology="single",
                profile="sol",
                reasoning_effort="low",
                idle=True,
            )
            self.assertNotIn(
                "delegate_agent",
                tuple(
                    tool.name
                    for tool in application.controller._engine._actions.tools()
                ),
            )
            await application.foreground_tasks._runtime_resolver(task.contract)
            self.assertEqual(application.model.current.profile.name, "terra")
            self.assertEqual(application.mode.topology.value, "team")
            self.assertEqual(application.mode.effective_reasoning_effort, "max")
            await application.aclose()


if __name__ == "__main__":
    unittest.main()
