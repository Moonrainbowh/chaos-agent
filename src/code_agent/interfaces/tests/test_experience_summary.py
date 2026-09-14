from __future__ import annotations

import unittest
from types import SimpleNamespace

from code_agent.interfaces.experience_summary import (
    ArtifactRef,
    build_experience_snapshot,
    format_experience_summary,
)
from code_agent.verification.evidence import EvidenceOutcome, EvidenceProvenance, EvidenceRecord


def evidence(identifier: str, outcome: EvidenceOutcome) -> EvidenceRecord:
    return EvidenceRecord(identifier, identifier, outcome, EvidenceProvenance.SYSTEM_VERIFIER, 1, "subject", "output", "ok")


class ExperienceSummaryTests(unittest.TestCase):
    def test_changed_task_without_evidence_is_not_reported_completed(self) -> None:
        state = SimpleNamespace(status="completed", diff="""diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-old
+new
""")
        snapshot = build_experience_snapshot(state)
        self.assertEqual(snapshot.status, "verifying")
        self.assertFalse(snapshot.can_claim_completion)
        self.assertIn("Verification: not run", format_experience_summary(snapshot))

    def test_summary_keeps_artifact_large_content_out_of_panel(self) -> None:
        state = SimpleNamespace(status="completed", diff=None)
        snapshot = build_experience_snapshot(state, evidence=(evidence("tests", EvidenceOutcome.PASS),), artifacts=(ArtifactRef("full log", "artifact://log/1", "487 lines"),))
        text = format_experience_summary(snapshot)
        self.assertIn("Verification: 1 passed, 0 failed", text)
        self.assertIn("Artifacts: full log", text)
        self.assertNotIn("487 lines", text)

    def test_error_with_changes_is_partial_and_actionable(self) -> None:
        state = SimpleNamespace(status="error", diff="""--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-a\n+b\n""")
        snapshot = build_experience_snapshot(state, error="ModelStreamError: stream closed")
        self.assertEqual(snapshot.status, "accepted_partial")
        self.assertEqual(snapshot.changed.sensitive_paths, (".env",))
        self.assertIn("current workspace", snapshot.next_action)

