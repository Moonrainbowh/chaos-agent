from __future__ import annotations

from collections.abc import Sequence

from code_agent.core.completion_contract import TaskContractRevision
from code_agent.verification.evidence import EvidenceRecord


def render_evidence_summary(contract: TaskContractRevision, generation: int, subject_hash: str, evidence: Sequence[EvidenceRecord], token_budget: int) -> str:
    if not isinstance(contract, TaskContractRevision) or isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise TypeError("contract and generation must be valid")
    if not isinstance(subject_hash, str) or not subject_hash or isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget < 1:
        raise ValueError("subject hash and token budget must be valid")
    relevant = [item for item in evidence if isinstance(item, EvidenceRecord) and item.generation == generation and item.subject_hash == subject_hash]
    by_criterion = {item.criterion_id: item for item in relevant}
    lines = [f"generation: {generation}"]
    for criterion in contract.criteria:
        record = by_criterion.get(criterion.identifier)
        if record is None:
            lines.append(f"unmet: {criterion.identifier}")
        else:
            lines.append(f"evidence: {criterion.identifier}={record.outcome.value}")
    result = "\n".join(lines)
    return result[: token_budget * 4]
