from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Mapping

from .models import ActionResult, ToolCall
from .action_semantics import READ_ONLY_TOOLS, operation_kind
from ._json import plain


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
    """Converge action turns without fresh Host facts, regardless of prose.

    The engine supplies facts reconstructed from durable paired results and
    subject state. Exact repeated reads remain the separate hard-stop signal.
    Thresholds ask for a bounded summary, never manufacture completion proof.
    """

    def __init__(
        self,
        *,
        warn_at: int = 3,
        force_at: int = 5,
        max_correction_failures: int = 3,
    ) -> None:
        if warn_at < 1 or force_at <= warn_at:
            raise ValueError("invalid tool-only convergence thresholds")
        self.warn_at, self.force_at = warn_at, force_at
        self._count = 0
        self.warn_at = warn_at
        self.force_at = force_at
        self.max_correction_failures = max_correction_failures
        self.exploration_count = 0
        self.correction_count = 0

    @property
    def _count(self) -> int:
        return self.exploration_count

    @_count.setter
    def _count(self, value: int) -> None:
        self.exploration_count = value

    def observe(
        self,
        *,
        has_text: bool,
        calls: tuple[ToolCall, ...] | list[ToolCall],
        has_validation_error: bool = False,
        has_host_progress: bool = False,
    ) -> ToolOnlyObservation | None:
        if not calls:
            return None

        if has_validation_error:
            self.correction_count += 1
            if self.correction_count >= self.max_correction_failures:
                return ToolOnlyObservation(
                    "finalize",
                    "task encountered repeated tool argument/contract validation errors "
                    f"for {self.correction_count} consecutive turns; resolve with a summary "
                    "or change approach instead of repeating invalid calls",
                    self.correction_count,
                )
            return None

        if has_host_progress:
            self.exploration_count = 0
            self.correction_count = 0
            return None

        self.correction_count = 0
        self.exploration_count += 1
        if self.exploration_count == self.force_at:
            return ToolOnlyObservation(
                "finalize",
                "task produced no new Host-observed progress for "
                f"{self.exploration_count} consecutive action turns; resolve from the "
                "current evidence instead of continuing broad exploration",
                self.exploration_count,
            )
        if self.exploration_count == self.warn_at:
            return ToolOnlyObservation(
                "warn",
                "task produced no new Host-observed progress for "
                f"{self.exploration_count} consecutive action turns; summarize current "
                "evidence and avoid broad repeated exploration",
                self.exploration_count,
            )
        return None

    def reset(self) -> None:
        self.exploration_count = 0
        self.correction_count = 0


class ExplorationRepeatObserver:
    """Bounded, per-run exact-result repetition observer for read-only tools."""

    def __init__(self, *, warn_at: int = 2, pause_at: int = 3, capacity: int = 64) -> None:
        if warn_at < 1 or pause_at <= warn_at or capacity < 1:
            raise ValueError("invalid exploration repeat thresholds")
        self.warn_at, self.pause_at, self.capacity = warn_at, pause_at, capacity
        self._recent: OrderedDict[str, tuple[str, int, str]] = OrderedDict()

    def observe(self, call: ToolCall, result: ActionResult) -> RepeatObservation | None:
        if operation_kind(call) != "read":
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
        return json.dumps(plain({str(k): value[k] for k in sorted(value, key=str) if str(k) not in ignored}), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(plain(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _target(call: ToolCall) -> str:
    value = call.arguments.get("path") or call.arguments.get("pattern") or call.arguments.get("root") or "target"
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|.)", "", str(value))
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:160]
