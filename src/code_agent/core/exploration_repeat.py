from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Mapping

from .models import ActionResult, ToolCall

READ_ONLY_TOOLS = frozenset({"read_file", "search_text", "list_files"})


@dataclass(frozen=True)
class RepeatObservation:
    kind: str
    reason: str
    count: int


@dataclass(frozen=True)
class ToolOnlyObservation:
    """Bounded convergence signal for a task with no answer text or progress."""

    kind: str
    reason: str
    count: int


class ToolOnlyConvergenceGuard:
    """Request an evidence checkpoint during extended tool-only exploration.

    This guard intentionally observes turn shape, not fuzzy result similarity.
    It is not a proof of a loop: a multi-step read-only investigation can need
    several distinct tools. Exact repeated reads remain the separate hard-stop
    signal; this guard only asks the model to state what it has learned and
    constrain any further evidence gathering.
    """

    def __init__(self, *, warn_at: int = 3, force_at: int = 5) -> None:
        if warn_at < 1 or force_at <= warn_at:
            raise ValueError("invalid tool-only convergence thresholds")
        self.warn_at, self.force_at = warn_at, force_at
        self._count = 0

    def observe(
        self,
        *,
        has_text: bool,
        calls: tuple[ToolCall, ...] | list[ToolCall],
    ) -> ToolOnlyObservation | None:
        if has_text or not calls or any(call.name in _PROGRESS_TOOLS for call in calls):
            self._count = 0
            return None
        self._count += 1
        if self._count == self.force_at:
            return ToolOnlyObservation(
                "replan",
                "task produced no answer text or edit/verification progress for "
                f"{self._count} consecutive tool-only turns; state the evidence "
                "and remaining gap before any further targeted tool calls",
                self._count,
            )
        if self._count == self.warn_at:
            return ToolOnlyObservation(
                "warn",
                "task produced no answer text or edit/verification progress for "
                f"{self._count} consecutive tool-only turns; summarize current "
                "evidence and avoid broad repeated exploration",
                self._count,
            )
        return None

    def reset(self) -> None:
        self._count = 0


_PROGRESS_TOOLS = frozenset({
    "write_file",
    "replace_text",
    "apply_workspace_edit_plan_v1",
    "run_verification",
})


class ExplorationRepeatObserver:
    """Bounded, per-run exact-result repetition observer for read-only tools."""

    def __init__(self, *, warn_at: int = 2, pause_at: int = 3, capacity: int = 64) -> None:
        if warn_at < 1 or pause_at <= warn_at or capacity < 1:
            raise ValueError("invalid exploration repeat thresholds")
        self.warn_at, self.pause_at, self.capacity = warn_at, pause_at, capacity
        self._recent: OrderedDict[str, tuple[str, int, str]] = OrderedDict()

    def observe(self, call: ToolCall, result: ActionResult) -> RepeatObservation | None:
        if call.name not in READ_ONLY_TOOLS:
            return None
        signature = f"{call.name}:{_canonical(call.arguments)}"
        fingerprint = _canonical(result.output)
        previous = self._recent.get(signature)
        count = previous[1] + 1 if previous is not None and previous[0] == fingerprint else 1
        self._recent.pop(signature, None)
        self._recent[signature] = (fingerprint, count, _target(call))
        while len(self._recent) > self.capacity:
            self._recent.popitem(last=False)
        if count >= self.pause_at:
            kind = "pause"
        elif count >= self.warn_at:
            kind = "warn"
        else:
            return None
        return RepeatObservation(kind, f"{call.name} {_target(call)} repeated {count} times with unchanged result; no result change was observed", count)


def _canonical(value: object) -> str:
    if isinstance(value, Mapping):
        ignored = {"request_id", "duration", "duration_ms", "call_id"}
        return json.dumps({str(k): value[k] for k in sorted(value, key=str) if str(k) not in ignored}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _target(call: ToolCall) -> str:
    value = call.arguments.get("path") or call.arguments.get("pattern") or call.arguments.get("root") or "target"
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|.)", "", str(value))
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:160]
