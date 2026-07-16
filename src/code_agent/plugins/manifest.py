from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence, cast

from code_agent.core._json import JSONValue
from code_agent.orchestration.models import AgentMode, ReasoningEffort

from .models import (
    ActionProposal,
    AgentContribution,
    CommandContribution,
    EventSubscription,
    ModeContribution,
    PluginContributions,
    PluginManifest,
    PluginRisk,
    ToolContribution,
    UiPrimitive,
    UiRequest,
)


_MAX_MANIFEST_BYTES = 256 * 1024
_TOP_KEYS = frozenset({"id", "namespace", "version", "host_api", "digest", "enabled", "contributions"})


class ManifestError(ValueError):
    pass


class PluginTrustStore:
    def __init__(self, trusted_digests: Mapping[str, str] | None = None) -> None:
        self._trusted = dict(trusted_digests or {})

    def is_trusted(self, identifier: str, digest: str) -> bool:
        return self._trusted.get(identifier) == digest


def load_manifest(
    path: Path,
    trust_store: PluginTrustStore,
    *,
    host_api: str,
    approved: bool = False,
) -> PluginManifest:
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise ManifestError("manifest must be a bounded regular file")
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"invalid manifest: {type(error).__name__}") from error
    if not isinstance(raw, dict):
        raise ManifestError("manifest root must be an object")
    return parse_manifest(raw, str(path.resolve()), trust_store, host_api=host_api, approved=approved)


def parse_manifest(
    raw: Mapping[str, object],
    source: str,
    trust_store: PluginTrustStore,
    *,
    host_api: str,
    approved: bool = False,
) -> PluginManifest:
    unknown = set(raw) - _TOP_KEYS
    missing = _TOP_KEYS - set(raw)
    if unknown or missing:
        raise ManifestError("manifest keys do not match the versioned schema")
    digest = raw["digest"]
    if not isinstance(digest, str) or digest != manifest_digest(raw):
        raise ManifestError("manifest digest mismatch")
    if raw["host_api"] != host_api:
        raise ManifestError("plugin host API is incompatible")
    identifier = cast(str, raw["id"])
    trusted = trust_store.is_trusted(identifier, digest)
    enabled = bool(raw["enabled"]) and (trusted or approved)
    if not isinstance(raw["enabled"], bool):
        raise ManifestError("enabled must be bool")
    try:
        contributions = _parse_contributions(
            _mapping(raw["contributions"], "contributions")
        )
        return PluginManifest(
            identifier,
            cast(str, raw["namespace"]),
            cast(str, raw["version"]),
            cast(str, raw["host_api"]),
            digest,
            source,
            trusted,
            enabled,
            contributions,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ManifestError(str(error)) from error


def manifest_digest(raw: Mapping[str, object]) -> str:
    payload = {key: value for key, value in raw.items() if key != "digest"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_contributions(raw: Mapping[str, object]) -> PluginContributions:
    allowed = {"tools", "commands", "modes", "agents", "events"}
    if set(raw) - allowed:
        raise ManifestError("unknown contribution category")
    return PluginContributions(
        tools=tuple(_tool(item) for item in _objects(raw.get("tools", []), "tools")),
        commands=tuple(_command(item) for item in _objects(raw.get("commands", []), "commands")),
        modes=tuple(_mode(item) for item in _objects(raw.get("modes", []), "modes")),
        agents=tuple(_agent(item) for item in _objects(raw.get("agents", []), "agents")),
        events=tuple(_event(item) for item in _objects(raw.get("events", []), "events")),
    )


def _tool(raw: Mapping[str, object]) -> ToolContribution:
    _exact(raw, {"id", "description", "target", "risk", "input_schema"})
    return ToolContribution(
        cast(str, raw["id"]),
        cast(str, raw["description"]),
        cast(str, raw["target"]),
        PluginRisk(cast(str, raw["risk"])),
        cast(Mapping[str, JSONValue], _mapping(raw["input_schema"], "input_schema")),
    )


def _command(raw: Mapping[str, object]) -> CommandContribution:
    _exact(raw, {"id", "description", "controller"})
    return CommandContribution(cast(str, raw["id"]), cast(str, raw["description"]), cast(str, raw["controller"]))


def _mode(raw: Mapping[str, object]) -> ModeContribution:
    _exact(raw, {"id", "base_mode", "prompt_policy", "tool_names", "reasoning_effort"})
    effort = raw["reasoning_effort"]
    return ModeContribution(
        cast(str, raw["id"]),
        AgentMode(cast(str, raw["base_mode"])),
        cast(str, raw["prompt_policy"]),
        _strings(raw["tool_names"], "tool_names"),
        None if effort is None else ReasoningEffort(cast(str, effort)),
    )


def _agent(raw: Mapping[str, object]) -> AgentContribution:
    _exact(raw, {"id", "base_mode", "instructions", "tool_names", "may_write"})
    return AgentContribution(
        cast(str, raw["id"]),
        AgentMode(cast(str, raw["base_mode"])),
        cast(str, raw["instructions"]),
        _strings(raw["tool_names"], "tool_names"),
        cast(bool, raw["may_write"]),
    )


def _event(raw: Mapping[str, object]) -> EventSubscription:
    _exact(raw, {"id", "event_kinds", "ui", "action"})
    ui_raw, action_raw = raw["ui"], raw["action"]
    ui = None if ui_raw is None else _ui(_mapping(ui_raw, "ui"))
    action = None if action_raw is None else _action(_mapping(action_raw, "action"))
    return EventSubscription(cast(str, raw["id"]), _strings(raw["event_kinds"], "event_kinds"), ui, action)


def _ui(raw: Mapping[str, object]) -> UiRequest:
    _exact(raw, {"primitive", "title", "prompt", "options"})
    return UiRequest(UiPrimitive(cast(str, raw["primitive"])), cast(str, raw["title"]), cast(str, raw["prompt"]), _strings(raw["options"], "options"))


def _action(raw: Mapping[str, object]) -> ActionProposal:
    _exact(raw, {"target", "arguments"})
    return ActionProposal(cast(str, raw["target"]), cast(Mapping[str, JSONValue], _mapping(raw["arguments"], "arguments")))


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ManifestError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _objects(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ManifestError(f"{label} must be an array")
    return tuple(_mapping(item, label) for item in value)


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or any(not isinstance(item, str) for item in value):
        raise ManifestError(f"{label} must be a string array")
    return tuple(cast(Sequence[str], value))


def _exact(raw: Mapping[str, object], expected: set[str]) -> None:
    if set(raw) != expected:
        raise ManifestError("contribution keys do not match the schema")
