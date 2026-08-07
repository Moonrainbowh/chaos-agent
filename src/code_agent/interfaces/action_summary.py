from __future__ import annotations

from collections.abc import Mapping

from code_agent.core.models import ActionResult


_TOOL_LABELS = {
    "list_files": "List files",
    "read_file": "Read file",
    "search_text": "Search text",
    "write_file": "Write file",
    "replace_text": "Edit file",
    "run_command": "Run command",
    "run_verification": "Run verification",
}
_KEY_LABELS = {
    "path": "path", "paths": "paths", "root": "root", "count": "items",
    "files": "files", "lines": "lines",
    "matches": "matches", "changed": "changed", "returncode": "exit", "verification": "verification",
    "duration_ms": "ms", "error_type": "error",
}
_FILE_PREVIEW_LIMIT = 5


def action_summary(name: str, request: Mapping[str, object] | None, result: ActionResult) -> str:
    """Format allowlisted tool facts and a bounded, tool-specific preview."""
    arguments = request.get("arguments") if request else None
    title = action_activity(name, arguments)
    facts = _result_facts(name, result)
    if result.is_error:
        facts.append("failed")
    detail = " · ".join(dict.fromkeys(facts))
    lines = [title + (f"  {detail}" if detail else "")]
    lines.extend(_result_preview(name, result))
    return "\n".join(lines)


def action_activity(name: str, arguments: object) -> str:
    """Describe the active tool without exposing arbitrary argument content."""
    values = arguments if isinstance(arguments, Mapping) else {}
    label = _TOOL_LABELS.get(name, name)
    if name == "list_files":
        root = _bounded_scalar(values.get("root")) or "."
        return f"{label} in {root}"
    if name in {"read_file", "write_file", "replace_text"}:
        path = _bounded_scalar(values.get("path"))
        return f"{label} {path}" if path else label
    if name == "search_text":
        pattern = _bounded_scalar(values.get("pattern"), maximum=80)
        return f'{label} "{pattern}"' if pattern else label
    return label


def _result_facts(name: str, result: ActionResult) -> list[str]:
    if name == "list_files":
        count = result.metadata.get("count")
        truncated = result.metadata.get("truncated") is True
        facts: list[str] = []
        if isinstance(count, int) and not isinstance(count, bool):
            facts.append(f"{count}{'+' if truncated else ''} files")
        duration = _duration(result.metadata.get("duration_ms"))
        if duration:
            facts.append(duration)
        return facts
    return _facts(result.metadata)


def _result_preview(name: str, result: ActionResult) -> list[str]:
    if name != "list_files" or not isinstance(result.output, Mapping):
        return []
    files = result.output.get("files")
    if not isinstance(files, (list, tuple)):
        return []
    visible = [
        text
        for item in files[:_FILE_PREVIEW_LIMIT]
        if (text := _bounded_scalar(item)) is not None
    ]
    remaining = len(files) - len(visible)
    if remaining > 0:
        visible.append(f"… {remaining} more returned")
    return visible


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


def _duration(value: object) -> str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return None
    if value < 1000:
        return f"{round(value)}ms"
    return f"{value / 1000:.1f}s"


def _bounded_scalar(value: object, *, maximum: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    return value.replace("\r", " ").replace("\n", " ")[:maximum]
