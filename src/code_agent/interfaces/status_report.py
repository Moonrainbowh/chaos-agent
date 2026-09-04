from __future__ import annotations

from pathlib import Path
from typing import Any


def format_status(app: Any) -> str:
    runtime = getattr(app, "runtime_selection", None)
    task_modes = getattr(app, "task_modes", None)
    permissions = getattr(app, "permissions", None)
    root = getattr(app, "workspace_root", None) or Path.cwd()
    lines = [
        f"State: {app.state.status}",
        f"Task: {app.active_task_id or app.state.task_id or 'none'}",
        f"Thread: {app.current_thread_id or 'none'}",
    ]
    if runtime is not None:
        current = runtime.current
        lines.extend(
            (
                f"Model profile: {getattr(current, 'profile', 'unavailable')}",
                f"Model: {getattr(current, 'model', 'unavailable')}",
                f"Protocol: {getattr(current, 'protocol', 'unavailable')}",
                f"Agent topology: {getattr(current, 'topology', 'unavailable')}",
                f"Reasoning effort: {getattr(current, 'reasoning_effort', 'unavailable')}",
                "Max output tokens: " + _number(
                    getattr(current, "max_output_tokens", None)
                ),
            )
        )
    else:
        lines.append(f"Model: {app._current_model() or 'unavailable'}")
    lines.extend(
        (
            f"Task mode: {task_modes.current.name if task_modes else 'unavailable'}",
            f"Permission: {permissions.current.name if permissions else 'unavailable'}",
            f"Workspace: {Path(root).resolve()}",
        )
    )
    host = getattr(app, "host_runtime_summary", None)
    if isinstance(host, str) and host.strip():
        lines.append("Host runtime: " + host)
    return "\n".join(lines)


def _number(value: object) -> str:
    return f"{value:,}" if isinstance(value, int) else "unavailable"
