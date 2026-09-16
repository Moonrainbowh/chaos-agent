from __future__ import annotations

import unittest

from code_agent.core.errors import ModelStreamError
from code_agent.interfaces.runtime_errors import (
    explain_runtime_error,
    runtime_error_summary,
)
from code_agent.providers.errors import ProviderProtocolError


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
