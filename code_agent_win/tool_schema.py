from __future__ import annotations

import re
from collections.abc import Mapping, Sequence


def object_schema(
    properties: Mapping[str, object], required: Sequence[str] = ()
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def text_schema(*, minimum: int = 0, maximum: int | None = None) -> dict[str, object]:
    schema: dict[str, object] = {"type": "string", "minLength": minimum}
    if maximum is not None:
        schema["maxLength"] = maximum
    return schema


def nonempty_text_schema() -> dict[str, object]:
    return text_schema(minimum=1)


def integer_schema(minimum: int, maximum: int) -> dict[str, object]:
    return {"type": "integer", "minimum": minimum, "maximum": maximum}


def matches_schema(value: object, schema: Mapping[str, object]) -> bool:
    """Validate the strict JSON-schema subset used by built-in tools."""
    schema_type = schema.get("type")
    if schema_type == "string":
        return _matches_string(value, schema)
    if schema_type == "integer":
        minimum, maximum = schema.get("minimum"), schema.get("maximum")
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and isinstance(minimum, int)
            and isinstance(maximum, int)
            and minimum <= value <= maximum
        )
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "array":
        return _matches_array(value, schema)
    if schema_type == "object":
        return _matches_object(value, schema)
    return False


def _matches_string(value: object, schema: Mapping[str, object]) -> bool:
    minimum = schema.get("minLength", 0)
    maximum = schema.get("maxLength")
    if not (
        isinstance(value, str)
        and isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and len(value) >= minimum
        and (
            maximum is None
            or (isinstance(maximum, int) and not isinstance(maximum, bool) and len(value) <= maximum)
        )
    ):
        return False
    options = schema.get("enum")
    if isinstance(options, Sequence) and value not in options:
        return False
    pattern = schema.get("pattern")
    return pattern is None or (
        isinstance(pattern, str) and re.search(pattern, value) is not None
    )


def _matches_array(value: object, schema: Mapping[str, object]) -> bool:
    items = schema.get("items")
    minimum, maximum = schema.get("minItems", 0), schema.get("maxItems")
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and isinstance(items, Mapping)
        and isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and len(value) >= minimum
        and (
            maximum is None
            or (isinstance(maximum, int) and not isinstance(maximum, bool) and len(value) <= maximum)
        )
        and all(matches_schema(item, items) for item in value)
    )


def _matches_object(value: object, schema: Mapping[str, object]) -> bool:
    if not isinstance(value, Mapping):
        return False
    properties, required = schema.get("properties"), schema.get("required")
    if not isinstance(properties, Mapping) or not isinstance(required, Sequence):
        return False
    if any(not isinstance(key, str) for key in value):
        return False
    if any(field not in value for field in required):
        return False
    if schema.get("additionalProperties") is False and set(value).difference(properties):
        return False
    return all(
        key not in properties
        or (isinstance(properties[key], Mapping) and matches_schema(item, properties[key]))
        for key, item in value.items()
    )


__all__ = [
    "integer_schema",
    "matches_schema",
    "nonempty_text_schema",
    "object_schema",
    "text_schema",
]
