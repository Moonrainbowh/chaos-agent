from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from enum import Enum

from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition


CONTRACT_TOOL_NAME = "load_tool_contract"
HYBRID_EAGER_BUILTIN_NAMES = frozenset(
    {
        "read_file",
        "read_code_slices",
        "list_files",
        "search_text",
        "git_status",
        "git_diff",
    }
)
_SPACE = re.compile(r"\s+")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SUMMARY_LIMIT = 120


class CapabilityStrategy(str, Enum):
    LEGACY = "legacy"
    HYBRID = "hybrid"
    PROGRESSIVE = "progressive"


def progressive_tools(
    tools: Sequence[ToolDefinition],
    disclosed_tools: Mapping[str, str],
    *,
    strategy: CapabilityStrategy = CapabilityStrategy.HYBRID,
) -> tuple[ToolDefinition, ...]:
    """Project tool definitions according to the selected disclosure strategy."""
    checked = tuple(tools)
    if not all(isinstance(tool, ToolDefinition) for tool in checked):
        raise TypeError("tools must contain ToolDefinition values")
    if not isinstance(strategy, CapabilityStrategy):
        raise TypeError("strategy must be a CapabilityStrategy")
    loader = next(
        (tool for tool in checked if tool.name == CONTRACT_TOOL_NAME), None
    )
    if loader is None:
        return checked
    candidates = tuple(
        tool for tool in checked if tool.name != CONTRACT_TOOL_NAME
    )
    if strategy is CapabilityStrategy.LEGACY:
        return candidates
    disclosures = _checked_disclosures(disclosed_tools)
    selected = {
        tool.name
        for tool in candidates
        if disclosures.get(tool.name) == tool_definition_digest(tool)
    }
    if strategy is CapabilityStrategy.HYBRID:
        selected |= HYBRID_EAGER_BUILTIN_NAMES
    return (
        _directory_definition(loader, candidates),
        *(tool for tool in candidates if tool.name in selected),
    )


def contract_result(
    request: ActionRequest, tools: Sequence[ToolDefinition]
) -> ActionResult:
    """Resolve one current tool definition without executing that tool."""
    if not isinstance(request, ActionRequest):
        raise TypeError("request must be an ActionRequest")
    if request.name != CONTRACT_TOOL_NAME:
        raise ValueError("request is not a tool-contract request")
    if set(request.arguments) != {"name"}:
        return _contract_error(request, "name is the only supported argument")
    name = request.arguments.get("name")
    if not isinstance(name, str) or not name.strip():
        return _contract_error(request, "name must be non-blank text")
    definitions = {
        tool.name: tool
        for tool in tools
        if isinstance(tool, ToolDefinition) and tool.name != CONTRACT_TOOL_NAME
    }
    definition = definitions.get(name)
    if definition is None:
        return _contract_error(request, "tool contract is unavailable")
    return ActionResult(
        request.id,
        request.name,
        {
            "name": definition.name,
            "digest": tool_definition_digest(definition),
            "availability": "next_model_turn",
        },
        metadata={"disclosed_tool": name},
    )


def tool_definition_digest(definition: ToolDefinition) -> str:
    """Return a stable SHA-256 digest for one complete provider definition."""
    if not isinstance(definition, ToolDefinition):
        raise TypeError("definition must be a ToolDefinition")
    encoded = json.dumps(
        definition.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def disclosed_contract(result: ActionResult) -> tuple[str, str] | None:
    """Return a validated name/digest pair from a successful loader result."""
    if not isinstance(result, ActionResult):
        raise TypeError("result must be an ActionResult")
    if result.name != CONTRACT_TOOL_NAME or result.is_error:
        return None
    name = result.metadata.get("disclosed_tool")
    output = result.output
    if not isinstance(name, str) or not name.strip() or not isinstance(output, Mapping):
        return None
    digest = output.get("digest")
    if (
        output.get("name") != name
        or output.get("availability") != "next_model_turn"
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
    ):
        return None
    return name, digest


def disclosed_name(result: ActionResult) -> str | None:
    """Compatibility helper returning only a validated disclosed tool name."""
    disclosure = disclosed_contract(result)
    return disclosure[0] if disclosure is not None else None


def _directory_definition(
    loader: ToolDefinition, candidates: Sequence[ToolDefinition]
) -> ToolDefinition:
    names = [tool.name for tool in candidates]
    lines = tuple(
        f"- {tool.name} [{_access_kind(tool.name)}]: "
        f"{_summary(tool.description)}"
        for tool in candidates
    )
    description = _summary(loader.description)
    if lines:
        description += (
            "\nAvailable capabilities (load any capability whose full definition "
            "is not present):\n"
            + "\n".join(lines)
        )
    return ToolDefinition(
        loader.name,
        description,
        {
            "type": "object",
            "properties": {"name": {"type": "string", "enum": names}},
            "required": ["name"],
            "additionalProperties": False,
        },
    )


def _access_kind(name: str) -> str:
    lowered = name.casefold()
    if lowered.startswith(("read_", "list_", "search_", "git_", "plan_")):
        return "read"
    if lowered.startswith(("write_", "replace_", "apply_", "create_", "restore_")):
        return "edit"
    if lowered.startswith(("run_", "terminal.")):
        return "execute"
    if lowered.startswith(("delegate_", "send_", "rename_")):
        return "coordinate"
    if lowered.startswith(("mcp.", "plugin.")):
        return "extension"
    return "other"


def _summary(value: str) -> str:
    compact = _SPACE.sub(" ", value).strip()
    if len(compact) <= _SUMMARY_LIMIT:
        return compact
    return compact[: _SUMMARY_LIMIT - 1].rstrip() + "…"


def _checked_disclosures(values: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise TypeError("disclosed_tools must be a mapping")
    checked = dict(values)
    if not all(
        isinstance(name, str)
        and name.strip()
        and isinstance(digest, str)
        and _DIGEST.fullmatch(digest) is not None
        for name, digest in checked.items()
    ):
        raise ValueError("disclosed_tools must map non-blank names to SHA-256 digests")
    return checked


def _contract_error(request: ActionRequest, detail: str) -> ActionResult:
    return ActionResult(
        request.id,
        request.name,
        {"error": "tool contract could not be loaded", "detail": detail},
        is_error=True,
    )
