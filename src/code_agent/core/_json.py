from __future__ import annotations

import math
import re
from types import MappingProxyType
from typing import Mapping, Sequence, TypeAlias, Union


JSONScalar: TypeAlias = Union[None, bool, int, float, str]
JSONValue: TypeAlias = Union[
    JSONScalar,
    Mapping[str, "JSONValue"],
    Sequence["JSONValue"],
]

_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")


def validate_identifier(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if len(value) > 256:
        raise ValueError(f"{label} must be at most 256 characters")


def validate_name(value: object, label: str = "name") -> None:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if _NAME_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{label} must start with a letter or underscore and contain only "
            "letters, digits, underscore, dot, or hyphen"
        )


def validate_json(value: object, path: str = "value") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite numbers")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            validate_json(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_json(item, f"{path}[{index}]")
        return
    raise TypeError(f"{path} contains a non-JSON value: {type(value).__name__}")


def validate_json_mapping(value: object, path: str) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping")
    validate_json(value, path)


def freeze_json(value: JSONValue, path: str = "value") -> JSONValue:
    validate_json(value, path)
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: freeze_json(item, f"{path}.{key}") for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            freeze_json(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    return value


def freeze_mapping(
    value: Mapping[str, JSONValue], path: str
) -> Mapping[str, JSONValue]:
    frozen = freeze_json(value, path)
    if not isinstance(frozen, Mapping):
        raise TypeError(f"{path} must be a mapping")
    return frozen


def plain(value: JSONValue) -> JSONValue:
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value
