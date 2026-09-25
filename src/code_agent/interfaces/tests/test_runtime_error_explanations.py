from __future__ import annotations

import sqlite3
import unittest

from code_agent.context.errors import PromptBudgetError, RuleLimitError
from code_agent.core.errors import ContextBuildError, ModelStreamError
from code_agent.interfaces.runtime_errors import (
    explain_runtime_error,
    runtime_error_summary,
)
from code_agent.providers.errors import ProviderProtocolError
from code_agent.sessions.errors import SessionStorageError


class RuntimeErrorExplanationTests(unittest.TestCase):
    def test_explanation_describes_partial_side_effects_and_next_step(self) -> None:
        text = explain_runtime_error(
            RuntimeError("ModelStreamError: stream closed"),
            status="interrupted",
            changed=True,
            checkpoint_saved=True,
        )
        self.assertIn("Changes may already exist", text)
        self.assertIn("checkpoint was saved", text)
        self.assertIn("Next:", text)

    def test_explanation_does_not_claim_changes_without_host_fact(self) -> None:
        text = explain_runtime_error(RuntimeError("boom"), status="error")
        self.assertIn("No workspace change has been confirmed", text)
        self.assertNotIn("already exist", text)

    def test_summary_exposes_safe_provider_protocol_cause(self) -> None:
        try:
            raise ProviderProtocolError("Chat stream ended without a completion marker")
        except ProviderProtocolError as cause:
            error = ModelStreamError("model stream failed")
            error.__cause__ = cause

        self.assertEqual(
            runtime_error_summary(error),
            "ProviderProtocolError: Chat stream ended without a completion marker",
        )

    def test_summary_explains_a_busy_session_database_without_sql(self) -> None:
        error = SessionStorageError("SQLite session operation failed")
        error.__cause__ = sqlite3.OperationalError("database is locked")

        self.assertEqual(
            runtime_error_summary(error),
            "Session database is busy. Close other Chaos Agent sessions, then retry.",
        )

    def test_wrapped_context_budget_failure_explains_cause_and_recovery(self) -> None:
        error = ContextBuildError("context build failed")
        error.__cause__ = PromptBudgetError(
            "system_and_rules_tokens exceeds its configured ceiling"
        )

        explanation = explain_runtime_error(error, status="error")
        self.assertIn("System prompt and project instructions exceed", explanation)
        self.assertIn("inspect the project instructions", explanation)
        self.assertNotIn("inspect the failure details", explanation)

    def test_rule_failure_does_not_echo_workspace_path_or_instruction_text(self) -> None:
        error = ContextBuildError("context build failed")
        error.__cause__ = RuleLimitError(
            "project rule exceeds limit: C:\\private\\AGENTS.md secret"
        )

        explanation = explain_runtime_error(error, status="error")
        self.assertIn("Project instructions exceed the context rule limit", explanation)
        self.assertNotIn("C:\\private", explanation)
        self.assertNotIn("secret", explanation)
