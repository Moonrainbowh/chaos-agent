from __future__ import annotations

from pathlib import Path, PureWindowsPath

from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest


async def record_unknown_gap(
    capture: object | None,
    context: ActionExecutionContext | None,
    request: ActionRequest,
    cancellation: CancellationToken,
) -> None:
    if capture is None:
        return
    if context is None:
        raise TypeError("execution_context is required for capture")
    cancellation.raise_if_cancelled()
    await capture.record_gap(context, request, "unknown-writer")
    cancellation.raise_if_cancelled()


def plugin_requires_gap(
    plugins: object | None,
    original: ActionRequest,
    translated: ActionRequest,
) -> bool:
    if plugins is None or original is translated:
        return False
    if translated.name in {"write_file", "replace_text"}:
        return False
    return plugins.risk_map().get(original.name) in {"write", "critical"}


def mcp_requires_gap(mcp: object | None, name: str) -> bool:
    return mcp is not None and mcp.risks().get(name) in {"write", "critical"}


def is_external_plan(path: str) -> bool:
    return Path(path).is_absolute() or PureWindowsPath(path).is_absolute()


__all__ = [
    "is_external_plan",
    "mcp_requires_gap",
    "plugin_requires_gap",
    "record_unknown_gap",
]
