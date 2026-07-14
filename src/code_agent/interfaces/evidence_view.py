from __future__ import annotations

from collections.abc import Sequence

from code_agent.verification.evidence import EvidenceRecord


def format_evidence_summary(records: Sequence[object]) -> str:
    evidence = [item for item in records if isinstance(item, EvidenceRecord)]
    if not evidence:
        return "未验证"
    lines = []
    for item in evidence[-8:]:
        lines.append(f"{item.criterion_id}: {item.outcome.value} ({item.provenance.value})")
    return "\n".join(lines)
