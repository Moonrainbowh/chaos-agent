from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.task import TaskAuthorization

from code_agent.orchestration.models import AgentDefinition, AgentMode, AgentRole
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from chaos_agent.runtime_dispatcher_factory import RuntimeDispatcherFactory
from chaos_agent.runtime_provider_controls import ProviderControls
from chaos_agent.runtime_controls import _initial_runtime


class _Client:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        await asyncio.sleep(0)
        self.closed = True


def _profile() -> ModelProfile:
    return ModelProfile(
        "sol",
        ProviderConfig(
            "https://api.example.test",
            "model-sol",
            ApiProtocol.RESPONSES,
            "TEST_KEY",
        ),
        8_000,
        1_000,
    )


def _snapshot(profile: ModelProfile):
    registry = ModeRegistry(
        standard_mode_definitions(
            {mode: profile.name for mode in AgentMode},
            tools_by_mode={mode: ("read_file",) for mode in AgentMode},
        )
    )
    return registry.freeze_runtime(
        "medium",
        {profile.name: profile},
        profile_id=profile.name,
        topology="single",
        reasoning_effort="medium",
    )


class PartialBuildCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_partial_client_closes_for_error_and_cancellation(self):
        profile, snapshot = _profile(), _snapshot(_profile())
        for failure in (RuntimeError("initial context failed"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                client = _Client()
                def fail(*args):
                    raise failure
                factory = RuntimeDispatcherFactory(root=Path.cwd(),
                    profiles={profile.name: profile}, client_factory=lambda *a, **k: client,
                    context_for=fail, dispatcher=object(), sessions=object(),
                    plugin_bridge=object(), plugin_bindings=object())
                with self.assertRaises(type(failure)):
                    _initial_runtime(Path.cwd(), snapshot, profile,
                        lambda *a, **k: client, fail, factory, object(), None)
                await asyncio.gather(*tuple(factory._partial_closures))
                self.assertTrue(client.closed)

    async def test_main_partial_client_closes_for_error_and_cancellation(self) -> None:
        profile = _profile()
        snapshot = _snapshot(profile)
        for failure in (RuntimeError("context failed"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                client = _Client()
                controls = ProviderControls.__new__(ProviderControls)
                controls._client_factory = lambda *_args, **_kwargs: client
                controls._build_snapshot = snapshot

                def fail(*_args):
                    raise failure

                controls._context_for = fail
                controls._context_wrapper = None
                with self.assertRaises(type(failure)):
                    await controls._build_runtime(profile)
                self.assertTrue(client.closed)

    async def test_child_partial_client_closes_for_error_and_cancellation(self) -> None:
        profile = _profile()
        snapshot = _snapshot(profile)
        agent = AgentDefinition(
            "child",
            AgentRole.SUBAGENT,
            snapshot,
            "Inspect one bounded concern.",
            ("read_file",),
        )
        for failure in (RuntimeError("context failed"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                client = _Client()

                def fail(*_args, agent_instructions):
                    self.assertEqual(agent_instructions, agent.instructions)
                    raise failure

                with tempfile.TemporaryDirectory() as directory:
                    factory = RuntimeDispatcherFactory(
                        root=Path(directory),
                        profiles={profile.name: profile},
                        client_factory=lambda *_args, **_kwargs: client,
                        context_for=SimpleNamespace(for_child=fail),
                        dispatcher=object(),
                        sessions=SimpleNamespace(for_owner=lambda _: object()),
                        plugin_bridge=object(),
                        plugin_bindings=object(),
                    )
                    with self.assertRaises(type(failure)):
                        factory.child_engine(agent, ActionExecutionContext("parent", "parent", "delegate", "task"),
                            TaskAuthorization(directory))
                    await asyncio.gather(*tuple(factory._partial_closures))
                    self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
