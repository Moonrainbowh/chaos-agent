from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from code_agent.interfaces.tests.test_command_navigation import Runtime, make_app
from code_agent_win.application_model import Application
from code_agent_win.model_selection_preference import (
    ModelSelectionPreference,
    ModelSelectionPreferenceStore,
)


class _Runtime:
    def __init__(self) -> None:
        self.used: list[str] = []

    def profiles(self):
        return (
            ("default", "default-model", "responses"),
            ("saved", "saved-model", "responses"),
        )

    async def use(self, *, profile: str, idle: bool) -> None:
        self.used.append(profile)


class _Tui:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def _append(self, _kind: object, message: str) -> None:
        self.messages.append(message)


def _application(store: ModelSelectionPreferenceStore, runtime: _Runtime, authentication=None) -> Application:
    return Application(
        controller=object(), foreground_tasks=object(), tui=_Tui(), dispatcher=object(),
        model=object(), runtime_selection=runtime, model_preferences=store,
        authentication=authentication, restore_model_selection=True,
    )


class ModelSelectionPreferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_command_remembers_successful_configured_selection(self) -> None:
        class Authentication:
            @staticmethod
            def preference_for_profile(profile: str) -> ModelSelectionPreference:
                return ModelSelectionPreference.configured(profile)

        with tempfile.TemporaryDirectory() as directory:
            app = make_app(runtime_selection=Runtime())
            app.authentication = Authentication()
            app.model_preferences = ModelSelectionPreferenceStore(Path(directory))

            self.assertTrue(await app.submit("/model gpt56_terra"))
            self.assertEqual(
                app.model_preferences.load(),
                ModelSelectionPreference.configured("gpt56_terra"),
            )

    async def test_configured_selection_is_persisted_and_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ModelSelectionPreferenceStore(Path(directory))
            store.save(ModelSelectionPreference.configured("saved"))

            contents = json.loads((Path(directory) / "last-model-selection.json").read_text())
            self.assertEqual(contents, {"version": 1, "source": "configured", "profile": "saved", "provider": None, "authentication": None, "model": None})
            self.assertFalse((Path(directory) / "last-model-selection.json.tmp").exists())

            runtime = _Runtime()
            application = _application(store, runtime)
            await application.startup()

            self.assertEqual(runtime.used, ["saved"])
            self.assertEqual(application.tui.messages, ["Restored model: saved"])

    async def test_saved_login_selection_restores_and_keeps_its_preference(self) -> None:
        class Authentication:
            def __init__(self) -> None:
                self.instructions: list[str] = []

            async def select_model(self, instruction: str) -> str:
                self.instructions.append(instruction)
                return "login/workbuddy/oauth/cloud-test"

            @staticmethod
            def is_unavailable_saved_model(_error: BaseException) -> bool:
                return False

        with tempfile.TemporaryDirectory() as directory:
            store = ModelSelectionPreferenceStore(Path(directory))
            preference = ModelSelectionPreference.saved_login("workbuddy", "oauth", "cloud-test")
            store.save(preference)
            runtime = _Runtime()
            authentication = Authentication()

            application = _application(store, runtime, authentication)
            await application.startup()

            self.assertEqual(authentication.instructions, ["workbuddy:oauth cloud-test"])
            self.assertEqual(runtime.used, ["login/workbuddy/oauth/cloud-test"])
            self.assertEqual(store.load(), preference)

    async def test_unavailable_saved_login_is_cleared_but_transient_failure_is_retained(self) -> None:
        class UnavailableAuthentication:
            async def select_model(self, _instruction: str) -> str:
                raise ValueError("missing")

            @staticmethod
            def is_unavailable_saved_model(_error: BaseException) -> bool:
                return True

        class TransientAuthentication:
            async def select_model(self, _instruction: str) -> str:
                raise RuntimeError("network")

            @staticmethod
            def is_unavailable_saved_model(_error: BaseException) -> bool:
                return False

        preference = ModelSelectionPreference.saved_login("workbuddy", "oauth", "cloud-test")
        for authentication, expected in ((UnavailableAuthentication(), None), (TransientAuthentication(), preference)):
            with self.subTest(authentication=type(authentication).__name__), tempfile.TemporaryDirectory() as directory:
                store = ModelSelectionPreferenceStore(Path(directory))
                store.save(preference)
                application = _application(store, _Runtime(), authentication)
                await application.startup()
                self.assertEqual(store.load(), expected)


if __name__ == "__main__":
    unittest.main()
