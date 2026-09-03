from __future__ import annotations

from collections.abc import Mapping

from code_agent.capabilities.catalog import (
    CapabilityStrategy,
    disclosed_contract,
    progressive_tools,
)

from .errors import ModelStreamError
from .events import AgentEvent, EventKind
from .models import ActionResult, ToolDefinition


def advertised_tools(
    dispatcher: object,
    allowed_names: frozenset[str] | None,
    disclosed_tools: Mapping[str, str],
    strategy: CapabilityStrategy,
) -> tuple[tuple[ToolDefinition, ...], set[str]]:
    try:
        tools = tuple(dispatcher.tools())
        if not all(isinstance(tool, ToolDefinition) for tool in tools):
            raise TypeError("action dispatcher exposed an invalid tool")
        names = {tool.name for tool in tools}
        if len(names) != len(tools):
            raise ModelStreamError("action dispatcher exposed duplicate tools")
        if allowed_names is not None:
            tools = tuple(tool for tool in tools if tool.name in allowed_names)
        projected = progressive_tools(
            tools, disclosed_tools, strategy=strategy
        )
        return projected, {tool.name for tool in projected}
    except ModelStreamError:
        raise
    except Exception:
        raise ModelStreamError("action dispatcher exposed invalid tools") from None


def disclosure_from_event(event: AgentEvent) -> tuple[str, str] | None:
    if event.kind is not EventKind.ACTION_COMPLETED:
        return None
    raw = event.payload.get("result")
    if not isinstance(raw, Mapping):
        return None
    try:
        return disclosed_contract(ActionResult.from_dict(raw))
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ("advertised_tools", "disclosure_from_event")
