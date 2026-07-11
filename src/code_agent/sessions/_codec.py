from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping, cast

from code_agent.core._json import JSONValue, plain
from code_agent.core.events import AgentEvent
from code_agent.core.models import Message
from code_agent.core.task_state import TaskState

from .errors import SessionCorruptionError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def encode_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def decode_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise SessionCorruptionError(f"{label} timestamp is not text")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise SessionCorruptionError(f"invalid {label} timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SessionCorruptionError(f"{label} timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def encode_message(message: Message) -> str:
    if not isinstance(message, Message):
        raise TypeError("message must be a Message")
    return _encode(message.to_dict())


def decode_message(payload: object) -> Message:
    try:
        return Message.from_dict(_decode_object(payload, "message"))
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted message") from error


def encode_event(event: AgentEvent) -> str:
    if not isinstance(event, AgentEvent):
        raise TypeError("event must be an AgentEvent")
    return _encode(event.to_dict())


def decode_event(payload: object) -> AgentEvent:
    try:
        return AgentEvent.from_dict(_decode_object(payload, "event"))
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted event") from error


def encode_task_state(state: TaskState) -> str:
    if not isinstance(state, TaskState):
        raise TypeError("state must be a TaskState")
    return _encode(state.to_dict())


def decode_task_state(payload: object) -> TaskState:
    try:
        return TaskState.from_dict(_decode_object(payload, "task state"))
    except SessionCorruptionError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise SessionCorruptionError("invalid persisted task state") from error


def encode_metadata(metadata: Mapping[str, JSONValue]) -> str:
    return _encode(cast(JSONValue, metadata))


def decode_metadata(payload: object) -> Mapping[str, JSONValue]:
    return cast(Mapping[str, JSONValue], _decode_object(payload, "metadata"))


def _encode(value: JSONValue) -> str:
    return json.dumps(
        plain(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _decode_object(payload: object, label: str) -> Mapping[str, object]:
    if not isinstance(payload, str):
        raise SessionCorruptionError(f"persisted {label} is not text")
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as error:
        raise SessionCorruptionError(f"invalid persisted {label} JSON") from error
    if not isinstance(value, dict):
        raise SessionCorruptionError(f"persisted {label} must be an object")
    return value
