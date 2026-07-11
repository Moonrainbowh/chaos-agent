from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Sequence, Tuple, cast

from ._json import (
    JSONValue,
    freeze_json,
    freeze_mapping,
    plain,
    validate_identifier,
    validate_json,
    validate_json_mapping,
    validate_name,
)
from .task_state import CommandFact, TaskState, TaskStateUpdate


_MESSAGE_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})
_CONTEXT_MEASUREMENT_KEYS = frozenset(
    {
        "prompt_tokens",
        "rule_tokens",
        "tool_tokens",
        "task_state_tokens",
        "repo_map_tokens",
        "message_tokens",
        "removed_message_count",
        "cache_hits",
        "cache_misses",
    }
)


def _validate_token_count(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        validate_identifier(self.id, "id")
        validate_name(self.name)
        validate_json_mapping(self.arguments, "arguments")
        object.__setattr__(
            self, "arguments", freeze_mapping(self.arguments, "arguments")
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": plain(self.arguments),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ToolCall:
        return cls(
            id=cast(str, data["id"]),
            name=cast(str, data["name"]),
            arguments=cast(Mapping[str, JSONValue], data["arguments"]),
        )


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def __post_init__(self) -> None:
        _validate_token_count(self.input_tokens, "input_tokens")
        _validate_token_count(self.output_tokens, "output_tokens")
        _validate_token_count(self.cached_input_tokens, "cached_input_tokens")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Usage:
        return cls(
            input_tokens=cast(int, data.get("input_tokens", 0)),
            output_tokens=cast(int, data.get("output_tokens", 0)),
            cached_input_tokens=cast(int, data.get("cached_input_tokens", 0)),
        )


@dataclass(frozen=True)
class Message:
    role: str
    content: str = ""
    name: Optional[str] = None
    tool_calls: Tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_call_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, str):
            raise TypeError("role must be a string")
        if self.role not in _MESSAGE_ROLES:
            raise ValueError(f"unsupported message role: {self.role!r}")
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")
        if self.name is not None:
            validate_name(self.name)
        if self.tool_call_id is not None:
            validate_identifier(self.tool_call_id, "tool_call_id")
        calls = tuple(self.tool_calls)
        if not all(isinstance(call, ToolCall) for call in calls):
            raise TypeError("tool_calls must contain only ToolCall values")
        object.__setattr__(self, "tool_calls", calls)

    def to_dict(self) -> dict[str, JSONValue]:
        result: dict[str, JSONValue] = {
            "role": self.role,
            "content": self.content,
        }
        if self.name is not None:
            result["name"] = self.name
        if self.tool_calls:
            result["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Message:
        calls = cast(Sequence[Mapping[str, object]], data.get("tool_calls", ()))
        return cls(
            role=cast(str, data["role"]),
            content=cast(str, data.get("content", "")),
            name=cast(Optional[str], data.get("name")),
            tool_calls=tuple(ToolCall.from_dict(call) for call in calls),
            tool_call_id=cast(Optional[str], data.get("tool_call_id")),
        )


@dataclass(frozen=True)
class ActionRequest:
    id: str
    name: str
    arguments: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        validate_identifier(self.id, "id")
        validate_name(self.name)
        validate_json_mapping(self.arguments, "arguments")
        object.__setattr__(
            self, "arguments", freeze_mapping(self.arguments, "arguments")
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": plain(self.arguments),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ActionRequest:
        return cls(
            id=cast(str, data["id"]),
            name=cast(str, data["name"]),
            arguments=cast(Mapping[str, JSONValue], data["arguments"]),
        )


@dataclass(frozen=True)
class ActionResult:
    request_id: str
    name: str
    output: JSONValue
    is_error: bool = False
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.request_id, "request_id")
        validate_name(self.name)
        validate_json(self.output, "output")
        if not isinstance(self.is_error, bool):
            raise TypeError("is_error must be a bool")
        validate_json_mapping(self.metadata, "metadata")
        object.__setattr__(self, "output", freeze_json(self.output, "output"))
        object.__setattr__(
            self, "metadata", freeze_mapping(self.metadata, "metadata")
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "request_id": self.request_id,
            "name": self.name,
            "output": plain(self.output),
            "is_error": self.is_error,
            "metadata": plain(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ActionResult:
        return cls(
            request_id=cast(str, data["request_id"]),
            name=cast(str, data["name"]),
            output=cast(JSONValue, data["output"]),
            is_error=cast(bool, data.get("is_error", False)),
            metadata=cast(Mapping[str, JSONValue], data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        validate_name(self.name)
        if not isinstance(self.description, str):
            raise TypeError("description must be a string")
        validate_json_mapping(self.parameters, "parameters")
        object.__setattr__(
            self, "parameters", freeze_mapping(self.parameters, "parameters")
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": plain(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ToolDefinition:
        return cls(
            name=cast(str, data["name"]),
            description=cast(str, data["description"]),
            parameters=cast(Mapping[str, JSONValue], data["parameters"]),
        )


class ModelEventKind(str, Enum):
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL = "tool_call"
    USAGE = "usage"
    COMPLETED = "completed"


@dataclass(frozen=True)
class ModelEvent:
    kind: ModelEventKind
    text: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    usage: Optional[Usage] = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ModelEventKind):
            raise TypeError("kind must be a ModelEventKind")
        if self.text is not None and not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if self.tool_call is not None and not isinstance(self.tool_call, ToolCall):
            raise TypeError("tool_call must be a ToolCall")
        if self.usage is not None and not isinstance(self.usage, Usage):
            raise TypeError("usage must be a Usage")
        expected = {
            ModelEventKind.TEXT_DELTA: "text",
            ModelEventKind.REASONING_DELTA: "text",
            ModelEventKind.TOOL_CALL: "tool_call",
            ModelEventKind.USAGE: "usage",
            ModelEventKind.COMPLETED: None,
        }[self.kind]
        present = {
            name
            for name, value in (
                ("text", self.text),
                ("tool_call", self.tool_call),
                ("usage", self.usage),
            )
            if value is not None
        }
        if present != ({expected} if expected else set()):
            raise ValueError(f"{self.kind.value} event has incompatible payload")

    def to_dict(self) -> dict[str, JSONValue]:
        result: dict[str, JSONValue] = {"kind": self.kind.value}
        if self.text is not None:
            result["text"] = self.text
        if self.tool_call is not None:
            result["tool_call"] = self.tool_call.to_dict()
        if self.usage is not None:
            result["usage"] = self.usage.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ModelEvent:
        call_data = cast(Optional[Mapping[str, object]], data.get("tool_call"))
        usage_data = cast(Optional[Mapping[str, object]], data.get("usage"))
        return cls(
            kind=ModelEventKind(cast(str, data["kind"])),
            text=cast(Optional[str], data.get("text")),
            tool_call=ToolCall.from_dict(call_data) if call_data else None,
            usage=Usage.from_dict(usage_data) if usage_data else None,
        )


@dataclass(frozen=True)
class ContextBundle:
    system_prompt: str
    messages: Tuple[Message, ...] = field(default_factory=tuple)
    measurements: Mapping[str, int] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.system_prompt, str):
            raise TypeError("system_prompt must be a string")
        messages = tuple(self.messages)
        if not all(isinstance(message, Message) for message in messages):
            raise TypeError("messages must contain only Message values")
        measurements = dict(self.measurements)
        if not set(measurements).issubset(_CONTEXT_MEASUREMENT_KEYS):
            raise ValueError("measurements contains an unsupported counter")
        for name, value in measurements.items():
            _validate_token_count(value, f"measurements.{name}")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "measurements", freeze_mapping(measurements, "measurements"))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "system_prompt": self.system_prompt,
            "messages": [message.to_dict() for message in self.messages],
            "measurements": dict(self.measurements),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> ContextBundle:
        messages = cast(Sequence[Mapping[str, object]], data.get("messages", ()))
        return cls(
            system_prompt=cast(str, data["system_prompt"]),
            messages=tuple(Message.from_dict(message) for message in messages),
            measurements=cast(Mapping[str, int], data.get("measurements", {})),
        )
