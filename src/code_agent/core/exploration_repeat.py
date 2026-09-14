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
