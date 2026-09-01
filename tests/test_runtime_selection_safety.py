from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from code_agent.config.loader import RuntimeConfig
from code_agent.orchestration.models import ModeSnapshot
from code_agent.policy.models import ApprovalMode
from code_agent.providers.anthropic import AnthropicClient
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent_win.app import create_application


def _profile(name: str, api: ApiProtocol) -> ModelProfile:
    return ModelProfile(
        name,
        ProviderConfig(
            "https://api.example.test",
            f"model-{name}",
            api,
            api_key_env="TEST_KEY",
        ),
        100_000,
        2_000,
    )


def _runtime(profiles: tuple[ModelProfile, ...]) -> RuntimeConfig:
    return RuntimeConfig(
        provider=profiles[0].provider,
        profile=profiles[0].name,
        approval_mode=ApprovalMode.AUTO,
        allow_sensitive_paths=False,
        config_path=Path("missing.toml"),
        profiles=profiles,
    )


def _application(
    container: Path, profiles: tuple[ModelProfile, ...], *, real_client: bool
):
    root, state = container / "workspace", container / "state"
    root.mkdir()
    state.mkdir()
    patches = (
        patch("code_agent_win.app._session_path", return_value=state / "sessions.sqlite3"),
        patch("code_agent_win.app._product_state_root", return_value=state),
        patch(
            "code_agent_win.app._workspace_storage_path",
            return_value=container / "managed-workspaces",
        ),
        patch("code_agent_win.app.load_runtime_config", return_value=_runtime(profiles)),
    )
    client = (
        patch("code_agent_win.app._model_client", side_effect=lambda _: object())
        if not real_client
        else None
    )
    with patches[0], patches[1], patches[2], patches[3]:
        if client is None:
            return create_application(root)
        with client:
            return create_application(root)


class RuntimeSelectionSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_tui_mode_commands_use_runtime_control_profiles(self) -> None:
        profiles = (
            _profile("sol", ApiProtocol.RESPONSES),
            _profile("terra", ApiProtocol.RESPONSES),
        )
        with tempfile.TemporaryDirectory() as directory:
            application = _application(Path(directory), profiles, real_client=False)

            self.assertTrue(await application.tui.submit("/模式"))
            self.assertIn("sol:model-sol", application.tui.state.entries[-1].text)
            self.assertTrue(await application.tui.submit("/模式 模型 terra"))
            self.assertEqual(application.runtime_selection.current.profile, "terra")
            await application.aclose()

    async def test_anthropic_application_omits_unmapped_effort(self) -> None:
        profiles = (_profile("claude", ApiProtocol.ANTHROPIC_MESSAGES),)
        with tempfile.TemporaryDirectory() as directory:
            application = _application(Path(directory), profiles, real_client=True)

            client = application.model.current.client
            self.assertIsInstance(client, AnthropicClient)
            self.assertIsNone(client._request_options.reasoning_effort)
            self.assertEqual(
                application.runtime_selection.current.reasoning_effort, "medium"
            )
            self.assertIn(
                "prompt-only; structured effort unsupported",
                application.tui._capability.lines()[1],
            )
            stable = application.model.current
            with self.assertRaisesRegex(ValueError, "no structured reasoning"):
                await application.runtime_selection.use(
                    reasoning_effort="high", idle=True
                )
            self.assertIs(application.model.current, stable)
            await application.aclose()

    async def test_nondefault_effort_cannot_be_carried_into_anthropic(self) -> None:
        profiles = (
            _profile("sol", ApiProtocol.RESPONSES),
            _profile("claude", ApiProtocol.ANTHROPIC_MESSAGES),
        )
        with tempfile.TemporaryDirectory() as directory:
            application = _application(Path(directory), profiles, real_client=False)
            await application.runtime_selection.use(
                reasoning_effort="max", idle=True
            )

            with self.assertRaisesRegex(ValueError, "no structured reasoning"):
                await application.runtime_selection.use(
                    profile="claude", idle=True
                )

            self.assertEqual(application.runtime_selection.current.profile, "sol")
            self.assertEqual(
                application.runtime_selection.current.reasoning_effort, "max"
            )
            await application.aclose()

    async def test_restricted_plugin_task_cannot_resume_as_broader_base_mode(self) -> None:
        profiles = (
            _profile("sol", ApiProtocol.RESPONSES),
            _profile("terra", ApiProtocol.RESPONSES),
        )
        with tempfile.TemporaryDirectory() as directory:
            application = _application(Path(directory), profiles, real_client=False)
            await application.runtime_selection.use(
                topology="team",
                profile="terra",
                reasoning_effort="max",
                idle=True,
            )
            base = application.mode
            restricted_base = ModeSnapshot(
                replace(base.definition, tool_names=("read_file",)),
                base.model,
                base.oracle_model,
                "a" * 64,
            )
            resolver = application.foreground_tasks._runtime_resolver
            controls = resolver.__self__
            await controls._apply_mode(restricted_base)
            restricted = application.mode
            self.assertEqual(restricted.profile_id, "terra")
            self.assertEqual(restricted.topology.value, "team")
            self.assertEqual(restricted.effective_reasoning_effort, "max")
            task = await application.foreground_tasks.start("restricted plugin task")
            self.assertEqual(task.contract.runtime_selection_digest, restricted.digest)

            await application.tui.modes.use("medium", idle=True)
            self.assertEqual(application.mode.profile_id, "terra")
            self.assertEqual(application.mode.topology.value, "team")
            self.assertEqual(application.mode.effective_reasoning_effort, "max")
            widened = application.mode
            with self.assertRaisesRegex(RuntimeError, "mode identity is unavailable"):
                await resolver(task.contract)

            self.assertEqual(application.mode.digest, widened.digest)
            await application.aclose()


if __name__ == "__main__":
    unittest.main()
