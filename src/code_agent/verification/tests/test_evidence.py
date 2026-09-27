from __future__ import annotations

import unittest

from code_agent.verification.evidence import EvidenceOutcome, EvidenceProvenance, EvidenceRecord, append_evidence, evidence_satisfies_current_verifier, evidence_satisfies_required


class EvidenceTests(unittest.TestCase):
    def record(self, outcome: EvidenceOutcome, provenance: EvidenceProvenance = EvidenceProvenance.SYSTEM_VERIFIER, **kwargs: object) -> EvidenceRecord:
        return EvidenceRecord.from_output("evidence-1", "tests", outcome, provenance, 2, "subject", "raw output", "diagnostic", **kwargs)

    def test_only_system_pass_or_manual_confirmation_satisfies_required(self) -> None:
        self.assertTrue(evidence_satisfies_required(self.record(EvidenceOutcome.PASS)))
        for outcome in (EvidenceOutcome.SKIPPED_BY_USER, EvidenceOutcome.UNAVAILABLE, EvidenceOutcome.UNSTABLE, EvidenceOutcome.ERROR):
            self.assertFalse(evidence_satisfies_required(self.record(outcome)))
        self.assertTrue(evidence_satisfies_required(self.record(EvidenceOutcome.USER_CONFIRMED, EvidenceProvenance.USER_CONFIRMATION, manual_only=True)))

    def test_evidence_is_serializable_append_only_and_redacts_sensitive_diagnostic(self) -> None:
        record = EvidenceRecord.from_output("evidence-1", "tests", EvidenceOutcome.FAIL, EvidenceProvenance.SYSTEM_VERIFIER, 1, "subject", "output", "api_key=secret")
        self.assertEqual(EvidenceRecord.from_dict(record.to_dict()), record)
        self.assertEqual(record.diagnostic, "[redacted diagnostic]")
        with self.assertRaises(ValueError):
            append_evidence((record,), record)

    def test_user_confirmation_requires_explicit_manual_condition(self) -> None:
        with self.assertRaises(ValueError):
            self.record(EvidenceOutcome.USER_CONFIRMED, EvidenceProvenance.USER_CONFIRMATION)

    def test_legacy_record_without_verifier_identity_remains_readable(self) -> None:
        record = EvidenceRecord.from_dict({
            "identifier": "evidence-1",
            "criterion_id": "tests",
            "outcome": "pass",
            "provenance": "system_verifier",
            "generation": 1,
            "subject_hash": "subject",
            "output_hash": "output",
            "diagnostic": "ok",
        })
        self.assertIsNone(record.verifier_identity)
        self.assertFalse(evidence_satisfies_current_verifier(record))
