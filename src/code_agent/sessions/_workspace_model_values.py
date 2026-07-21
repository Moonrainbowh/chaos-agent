from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Mapping

from code_agent.core._json import JSONValue, freeze_mapping, plain, validate_json
from code_agent.core.limits import TaskBudget
from code_agent.workspace._snapshot_manifest import SnapshotManifest, validate_manifest

from .models import GoalRecord


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
GIT_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
MAX_GOALS = 10_000
MAX_FILES = 100_000
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024


def require_uuid(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as error:
        raise ValueError(f"{name} must be a UUID") from error
    return parsed.hex


def require_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not value.strip() or len(value) > 1_024 or "\0" in value:
        raise ValueError(f"{name} must be bounded non-blank text")
    return value


def require_absolute_path(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    if not value or len(value) > 32_767 or "\0" in value:
        raise ValueError(f"{name} must be a bounded absolute path")
    if not PureWindowsPath(value).is_absolute() and not PurePosixPath(value).is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return value


def require_utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def require_json_mapping(value: object, name: str) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must contain mappings")
    validate_json_value(value, name)
    return freeze_mapping(value, name)  # type: ignore[arg-type]


def require_json_size(value: JSONValue) -> None:
    encoded = json.dumps(plain(value), ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("checkpoint cursor payload exceeds storage limit")


def validate_json_value(value: object, name: str) -> None:
    """Reject resource-heavy or non-portable values before freezing or decoding."""
    _validate_json_limits(value, name, 0, frozenset())
    validate_json(value, name)


def require_digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _validate_json_limits(
    value: object,
    path: str,
    depth: int,
    ancestors: frozenset[int],
) -> None:
    if depth > 16:
        raise ValueError(f"{path} exceeds maximum JSON depth")
    if isinstance(value, str):
        if len(value) > 32_767:
            raise ValueError(f"{path} contains oversized text")
        value.encode("utf-8")
    if isinstance(value, Mapping):
        if len(value) > 10_000 or id(value) in ancestors:
            raise ValueError(f"{path} has too many values or contains a cycle")
        parents = ancestors | {id(value)}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 1_024:
                raise ValueError(f"{path} has an invalid key")
            _validate_json_limits(item, f"{path}.{key}", depth + 1, parents)
    elif isinstance(value, (list, tuple)):
        if len(value) > 10_000 or id(value) in ancestors:
            raise ValueError(f"{path} has too many values or contains a cycle")
        parents = ancestors | {id(value)}
        for index, item in enumerate(value):
            _validate_json_limits(item, f"{path}[{index}]", depth + 1, parents)


def validate_snapshot_manifest(manifest: SnapshotManifest) -> None:
    try:
        validate_manifest(manifest, MAX_FILES, MAX_TOTAL_BYTES, MAX_FILE_BYTES)
    except Exception as error:
        raise ValueError("invalid workspace snapshot manifest") from error


def goal_payload(goal: GoalRecord) -> Mapping[str, JSONValue]:
    if not isinstance(goal, GoalRecord):
        raise TypeError("goals must contain GoalRecord values")
    return {
        "objective": goal.objective,
        "status": goal.status.value,
        "metadata": goal.metadata,
        "created_at": goal.created_at.isoformat(),
        "updated_at": goal.updated_at.isoformat(),
    }


def budget_payload(budget: TaskBudget) -> Mapping[str, JSONValue]:
    return {
        "model_name": budget.model_name,
        "max_agent_rounds": budget.limits.max_agent_rounds,
        "max_tool_calls": budget.limits.max_tool_calls,
        "max_tool_calls_per_round": budget.limits.max_tool_calls_per_round,
        "max_total_tokens": budget.limits.max_total_tokens,
        "model_turns": budget.model_turns,
        "tool_calls": budget.tool_calls,
        "input_tokens": budget.input_tokens,
        "output_tokens": budget.output_tokens,
        "repair_cycles": budget.repair_cycles,
        "repeated_failures": budget.repeated_failures,
        "last_failure_signature": budget.last_failure_signature,
        "active_seconds": budget.active_seconds,
        "warned_at_80": budget.warned_at_80,
        "warned_at_90": budget.warned_at_90,
    }
