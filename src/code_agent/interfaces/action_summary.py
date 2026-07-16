from __future__ import annotations

from collections.abc import Mapping

from code_agent.core.models import ActionResult


_KEY_LABELS = {
    "path": "path", "paths": "paths", "count": "items", "files": "files", "lines": "lines",
    "matches": "matches", "changed": "changed", "returncode": "exit", "verification": "verification",
    "duration_ms": "ms", "error_type": "error",
}


def action_summary(name: str, request: Mapping[str, object] | None, result: ActionResult) -> str:
    """Format only allowlisted, scalar tool facts for the terminal transcript."""
    facts = _facts(request.get("arguments") if request else None)
    facts.extend(_facts(result.metadata))
    if result.is_error:
        facts.append("failed")
    detail = " · ".join(dict.fromkeys(facts))
    return f"{name}" + (f"  {detail}" if detail else "")


def _facts(values: object) -> list[str]:
    if not isinstance(values, Mapping):
        return []
    facts: list[str] = []
    for key, label in _KEY_LABELS.items():
        value = values.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).replace("\r", " ").replace("\n", " ")
            if key in {"path", "paths"}:
                text = text[:160]
            elif key == "error_type":
                text = type(value).__name__ if not isinstance(value, str) else text[:80]
            facts.append(f"{text} {label}" if label == "ms" else f"{label}: {text}")
    return facts
