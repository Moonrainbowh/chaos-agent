from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import quantiles
from typing import Mapping

from code_agent.core._json import plain
from code_agent.core.models import ActionRequest, ActionResult


@dataclass(frozen=True)
class TaskActionMetrics:
    """Bounded task-level aggregation of local action execution facts."""

    actions: int
    errors: int
    total_duration_ms: int
    p50_duration_ms: float
    p95_duration_ms: float
    by_name: Mapping[str, int]
    category_duration_ms: Mapping[str, int]
    repeated_reads: int
    retries: int


class ActionMetricsCollector:
    """Collect action timing without retaining request arguments or output."""

    def __init__(self) -> None:
        self._durations: dict[str, list[float]] = defaultdict(list)
        self._errors: Counter[str] = Counter()
        self._names: dict[str, Counter[str]] = defaultdict(Counter)
        self._category_duration: dict[str, Counter[str]] = defaultdict(Counter)
        self._read_fingerprints: dict[tuple[str, int], set[str]] = defaultdict(set)
        self._read_calls: Counter[tuple[str, int]] = Counter()
        self._workspace_generations: Counter[str] = Counter()
        self._failed_signatures: set[str] = set()
        self._retries: Counter[str] = Counter()

    def record(self, task_id: str, request: ActionRequest, result: ActionResult) -> None:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be non-empty text")
        if not isinstance(request, ActionRequest) or not isinstance(result, ActionResult):
            raise TypeError("request and result must be action models")
        raw_duration = result.metadata.get("duration_ms", 0)
        if isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, float)):
            raise TypeError("duration_ms must be numeric")
        if not math.isfinite(raw_duration) or raw_duration < 0:
            raise ValueError("duration_ms must be finite and non-negative")
        duration = int(round(float(raw_duration)))
        key = _signature(request)
        self._durations[task_id].append(duration)
        self._names[task_id][request.name] += 1
        if result.is_error:
            self._errors[task_id] += 1
        self._category_duration[task_id][_category(request.name)] += duration
        attempt_key = f"{task_id}:{key}"
        if attempt_key in self._failed_signatures:
            self._retries[task_id] += 1
            self._failed_signatures.discard(attempt_key)
        if request.name in _READ_ACTIONS:
            generation = self._workspace_generations[task_id]
            generation_key = (task_id, generation)
            self._read_calls[generation_key] += 1
            self._read_fingerprints[generation_key].add(key)
        if result.is_error:
            self._failed_signatures.add(attempt_key)
        if request.name in _PROCESS_ACTIONS:
            self._workspace_generations[task_id] += 1
        elif (
            request.name in _SUCCESSFUL_MUTATIONS
            and not result.is_error
        ) or (
            request.name == "apply_workspace_edit_plan_v1"
            and (
                (
                    isinstance(result.output, Mapping)
                    and result.output.get("workspace_may_have_changed") is True
                )
                or result.metadata.get("workspace_may_have_changed") is True
            )
        ):
            self._workspace_generations[task_id] += 1

    def snapshot(self, task_id: str) -> TaskActionMetrics:
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be non-empty text")
        durations = tuple(self._durations.get(task_id, ()))
        return TaskActionMetrics(
            actions=len(durations),
            errors=self._errors.get(task_id, 0),
            total_duration_ms=sum(durations),
            p50_duration_ms=_percentile(durations, 0.50),
            p95_duration_ms=_percentile(durations, 0.95),
            by_name=dict(self._names.get(task_id, {})),
            category_duration_ms=dict(self._category_duration.get(task_id, {})),
            repeated_reads=sum(
                max(0, calls - len(self._read_fingerprints.get(generation_key, ())))
                for generation_key, calls in self._read_calls.items()
                if generation_key[0] == task_id
            ),
            retries=self._retries.get(task_id, 0),
        )


def _signature(request: ActionRequest) -> str:
    encoded = json.dumps(
        plain(request.arguments),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{request.name}\0{encoded}".encode("utf-8")).hexdigest()


_READ_ACTIONS = {"read_file", "read_code_slices", "search_text", "list_files"}
_SUCCESSFUL_MUTATIONS = {
    "write_file",
    "replace_text",
}
_PROCESS_ACTIONS = {
    "run_command",
    "run_process_v1",
}


def _category(name: str) -> str:
    if name.startswith("git_"):
        return "git"
    if name in _READ_ACTIONS:
        return "workspace_read"
    if name in {
        "write_file",
        "replace_text",
        "plan_workspace_edits_v1",
        "apply_workspace_edit_plan_v1",
    }:
        return "workspace_edit"
    if name in {"run_verification", "run_process_v1", "run_command"}:
        return "verification_or_process"
    return "other"


def _percentile(values: tuple[float, ...], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return quantiles(values, n=100, method="inclusive")[int(percentile * 100) - 1]
