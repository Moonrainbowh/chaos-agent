from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from .grader import Grade
from .models import ScenarioResult


@dataclass(frozen=True)
class EvaluationMetrics:
    total: int
    passed: int
    model_turns: int
    tool_calls: int

    @classmethod
    def from_results(cls, results: Sequence[tuple[ScenarioResult, Grade]]) -> "EvaluationMetrics":
        return cls(
            len(results),
            sum(grade.passed for _, grade in results),
            sum(result.model_turns for result, _ in results),
            sum(result.tool_calls for result, _ in results),
        )

    @classmethod
    def from_records(cls, records: Sequence[object]) -> "EvaluationMetrics":
        checked = tuple(records)
        traces = [getattr(record, "observation").trace for record in checked]
        return cls(
            len(checked),
            sum(getattr(record, "grade").passed for record in checked),
            sum(trace.model_turns or 0 for trace in traces),
            sum(trace.tool_calls or 0 for trace in traces),
        )

    def to_json(self) -> str:
        return json.dumps({"total": self.total, "passed": self.passed, "model_turns": self.model_turns, "tool_calls": self.tool_calls}, sort_keys=True)


def render_markdown_report(metrics: EvaluationMetrics) -> str:
    return "\n".join(("# Evaluation", "", f"- Passed: {metrics.passed}/{metrics.total}", f"- Model turns: {metrics.model_turns}", f"- Tool calls: {metrics.tool_calls}"))
