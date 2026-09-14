"""Metrics for evaluating the user experience on real engineering tasks.

The record is intentionally separate from the replay score.  A replay grade
answers whether a scenario was safe/correct; this record answers whether a
person could understand and finish the task.  Callers must populate it from
trusted runner/grader observations, never from model self-report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from collections.abc import Sequence


class FailureSource(str, Enum):
    MODEL = "model"
    AGENT = "agent"
    PROVIDER = "provider"
    INFRASTRUCTURE = "infrastructure"
    NONE = "none"


@dataclass(frozen=True)
class ExperienceTaskRecord:
    task_id: str
    category: str
    completed: bool
    verified: bool
    user_interventions: int = 0
    retries: int = 0
    false_completion: bool = False
    elapsed_seconds: int = 0
    model_tokens: int | None = None
    tool_calls: int = 0
    failure_source: FailureSource = FailureSource.NONE

    def __post_init__(self) -> None:
        for name in ("task_id", "category"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"{name} must be bounded non-blank text")
        for name in ("completed", "verified", "false_completion"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")
        for name in ("user_interventions", "retries", "elapsed_seconds", "tool_calls"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.model_tokens is not None:
            if isinstance(self.model_tokens, bool) or not isinstance(self.model_tokens, int) or self.model_tokens < 0:
                raise ValueError("model_tokens must be non-negative when collected")
        if not isinstance(self.failure_source, FailureSource):
            raise TypeError("failure_source must be a FailureSource")
        if self.completed and not self.verified and not self.false_completion:
            raise ValueError("an unverified completion must be marked false_completion")


@dataclass(frozen=True)
class ExperienceEvaluationMetrics:
    total: int
    completed: int
    verified: int
    interventions: int
    retries: int
    false_completions: int
    elapsed_seconds: int
    model_tokens: int | None
    tool_calls: int
    failures_by_source: dict[str, int]

    @classmethod
    def from_records(cls, records: Sequence[ExperienceTaskRecord]) -> "ExperienceEvaluationMetrics":
        checked = tuple(records)
        if not all(isinstance(item, ExperienceTaskRecord) for item in checked):
            raise TypeError("records must contain ExperienceTaskRecord values")
        sources = {source.value: 0 for source in FailureSource}
        for item in checked:
            sources[item.failure_source.value] += 1
        return cls(
            total=len(checked),
            completed=sum(item.completed for item in checked),
            verified=sum(item.verified for item in checked),
            interventions=sum(item.user_interventions for item in checked),
            retries=sum(item.retries for item in checked),
            false_completions=sum(item.false_completion for item in checked),
            elapsed_seconds=sum(item.elapsed_seconds for item in checked),
            model_tokens=(
                sum(item.model_tokens for item in checked if item.model_tokens is not None)
                if all(item.model_tokens is not None for item in checked)
                else None
            ),
            tool_calls=sum(item.tool_calls for item in checked),
            failures_by_source=sources,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "completed": self.completed,
            "verified": self.verified,
            "interventions": self.interventions,
            "retries": self.retries,
            "false_completions": self.false_completions,
            "elapsed_seconds": self.elapsed_seconds,
            "model_tokens": self.model_tokens,
            "tool_calls": self.tool_calls,
            "failures_by_source": dict(self.failures_by_source),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


def render_experience_metrics(metrics: ExperienceEvaluationMetrics) -> str:
    """Render a compact report without turning infrastructure failures into task failures."""
    lines = [
        "# Real-task experience evaluation",
        "",
        f"- Completed: {metrics.completed}/{metrics.total}",
        f"- Verified: {metrics.verified}/{metrics.total}",
        f"- User interventions: {metrics.interventions}",
        f"- Retries: {metrics.retries}",
        f"- False completions: {metrics.false_completions}",
        f"- Time: {metrics.elapsed_seconds}s; model tokens: {metrics.model_tokens if metrics.model_tokens is not None else 'not collected'}; tool calls: {metrics.tool_calls}",
        "- Failure sources: " + ", ".join(
            f"{key}={value}" for key, value in metrics.failures_by_source.items() if value
        ),
    ]
    return "\n".join(lines)
