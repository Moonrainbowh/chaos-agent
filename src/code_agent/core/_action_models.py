from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, cast

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
