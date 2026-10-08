"""New task context identity blocks drift; old task facts are never invented."""
import json
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from chaos_agent.app import create_application
from code_agent.config.loader import RuntimeConfig
from code_agent.context_windows.policy import WindowPolicy
from code_agent.core.task import TaskAuthorization, TaskContract, TaskStatus
from code_agent.interfaces.task_controller import freeze_task_contract, ForegroundTaskController
from code_agent.policy.models import ApprovalMode
from tests.test_runtime_selection import _profiles


@contextmanager
def isolated_application(policy=None):
    profiles = _profiles()
    profiles["sol"] = replace(profiles["sol"], context_policy=policy)
    config = RuntimeConfig(provider=profiles["sol"].provider, profile="sol",
        approval_mode=ApprovalMode.AUTO, allow_sensitive_paths=False,
        config_path=Path("missing.toml"), profiles=tuple(profiles.values()))
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        temporary = Path(directory).resolve()
        root, state = temporary / "workspace", temporary / "state"
        root.mkdir()
        state.mkdir()
        session = state / "explicit-test.sqlite3"
        assert session.resolve().is_relative_to(temporary)
        for name, value in (("_session_path", session), ("_product_state_root", state),
            ("_workspace_storage_path", temporary / "worktrees"), ("load_runtime_config", config)):
            stack.enter_context(patch("chaos_agent.app." + name, return_value=value))
        stack.enter_context(patch("chaos_agent.app._model_client", return_value=object()))
        yield create_application(root)


class ContextSelectionFreezeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Freeze the original cap so restore/drift counterexamples stay exact.
        scope = patch.dict("os.environ", {"CHAOS_MAX_PROMPT_TOKENS": "20000"})
        scope.start()
        self.addCleanup(scope.stop)

    async def test_new_real_host_tasks_persist_semantic_and_all_explicit_strategies(self):
        for strategy in ("semantic", "summary", "boundary", "persistent"):
            with self.subTest(strategy=strategy), isolated_application(None if strategy == "semantic" else
                WindowPolicy(strategy=strategy, work_tokens=32000, safety_tokens=64)) as app:
                try:
                    task = await app.tasks.start("Explain this project")
                    stored = await app.sessions.load_task(task.id)
                    facts = stored.contract.context_selection
                    self.assertEqual(facts["strategy"], strategy)
                    self.assertEqual(facts["host_prompt_tokens"], 20000)
                    self.assertEqual(facts["max_output_tokens"], 8000)
                    self.assertEqual(TaskContract.from_dict(stored.contract.to_dict()), stored.contract)
                    with self.assertRaises(TypeError):
                        facts["strategy"] = "persistent"
                    if facts["policy"] is not None:
                        with self.assertRaises(TypeError):
                            facts["policy"]["work_tokens"] = 100000
                    await app.tasks.restore_runtime_settings(task.id)
                finally:
                    await app.aclose()

    async def test_current_runtime_match_does_not_bypass_context_policy_or_capacity_drift(self):
        with isolated_application() as app:
            try:
                task = await app.tasks.start("Explain this project")
                # Existing Host test pattern: bound resolver identifies the actual controls.
                controls = app.foreground_tasks._runtime_resolver.__self__
                original = controls._profiles["sol"]
                for change in ({"context_policy": WindowPolicy(strategy="persistent", safety_tokens=64)},
                    {"context_window": 190000}, {"api_input_tokens": 50000}, {"max_output_tokens": 7000}):
                    with self.subTest(change=tuple(change)):
                        controls._profiles["sol"] = replace(original, **change)
                        with self.assertRaisesRegex(RuntimeError, "context selection"):
                            await app.tasks.restore_runtime_settings(task.id)
                controls._profiles["sol"] = original
                with patch.dict("os.environ", {"CHAOS_MAX_PROMPT_TOKENS": "21000"}):
                    with self.assertRaisesRegex(RuntimeError, "context selection"):
                        await app.tasks.restore_runtime_settings(task.id)
                await app.tasks.restore_runtime_settings(task.id)
                self.assertEqual((await app.sessions.load_task(task.id)).contract, task.contract)
            finally:
                await app.aclose()

    async def test_old_eight_fact_contract_keeps_configured_compatibility_without_fabrication(self):
        with isolated_application() as app:
            try:
                task = await app.tasks.start("Explain this project")
                old = replace(task.contract, context_selection=None)
                controls = app.foreground_tasks._runtime_resolver.__self__
                await controls.resolve_runtime_contract(old)
                self.assertIsNone(old.context_selection)
                facts = controls.profile_facts()
                self.assertIsNone(freeze_task_contract("explain", old.authorization, facts[:8]).context_selection)
                self.assertEqual(freeze_task_contract("explain", old.authorization, facts).context_selection,
                    task.contract.context_selection)
            finally:
                await app.aclose()

    async def test_snapshot_whitelist_versions_policy_and_secret_fields_are_validated(self):
        with isolated_application() as app:
            try:
                task = await app.tasks.start("Explain this project")
                facts = json.loads(json.dumps(task.contract.to_dict()["context_selection"]))
                for change in ({"api_key": "must-not-save"}, {"version": 99}, {"counter_version": "unknown"},
                    {"host_prompt_tokens": True}, {"strategy": "persistent"}, {"policy": {"api_key": "secret"}}):
                    with self.subTest(change=tuple(change)), self.assertRaises((ValueError, TypeError)):
                        replace(task.contract, context_selection={**facts, **change})
                self.assertIsNone(TaskContract.from_dict({"objective": "old", "authorization":
                    TaskAuthorization.local_workspace("explicit-old-workspace").to_dict()}).context_selection)
            finally:
                await app.aclose()

    async def test_repeated_resume_while_drift_waiting_does_not_repeat_transition_or_call_model(self):
        with isolated_application() as app:
            try:
                task = await app.tasks.start("Explain this project")
                controls = app.foreground_tasks._runtime_resolver.__self__
                controls._profiles["sol"] = replace(controls._profiles["sol"], api_input_tokens=10000)
                boundary = ForegroundTaskController(app.controller, app.sessions,
                    task.contract.authorization.workspace_root, runtime_resolver=controls.resolve_runtime_contract)
                for _ in range(2):
                    events = [event async for event in boundary.events(task.id)]
                    self.assertEqual(len(events), 1)
                    self.assertEqual(events[0].payload["status"], "waiting_decision")
                    self.assertEqual((await app.sessions.load_task(task.id)).status, TaskStatus.WAITING_DECISION)
                self.assertEqual(await app.sessions.context_records(task.thread_id, "usage"), ())
            finally:
                await app.aclose()
