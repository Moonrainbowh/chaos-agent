from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence, cast

from ._json import JSONValue


MAX_MESSAGE_ATTACHMENTS = 8
MAX_MESSAGE_ATTACHMENT_BYTES = 32 * 1024 * 1024
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPES = frozenset({"image/png", "text/plain"})


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value <= 0:
        raise ValueError(f"{label} must be positive")
    return value


def _display_name(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("display_name must be a string")
    if (
        not value
        or len(value) > 128
        or value in {".", ".."}
        or any(character in value for character in ("/", "\\", "\0"))
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("display_name must be a safe bounded file name")
    return value


@dataclass(frozen=True)
class AttachmentRef:
    """Safe metadata for one normalized, content-addressed attachment."""

    sha256: str
    media_type: str
    size_bytes: int
    display_name: str
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, str) or not _DIGEST.fullmatch(self.sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.media_type, str) or self.media_type not in _MEDIA_TYPES:
            raise ValueError("media_type is not supported")
        object.__setattr__(self, "size_bytes", _positive_int(self.size_bytes, "size_bytes"))
        object.__setattr__(self, "display_name", _display_name(self.display_name))
        dimensions = (self.width, self.height)
        if self.media_type == "image/png":
            if any(value is None for value in dimensions):
                raise ValueError("image attachments require dimensions")
            object.__setattr__(self, "width", _positive_int(self.width, "width"))
            object.__setattr__(self, "height", _positive_int(self.height, "height"))
        elif any(value is not None for value in dimensions):
            raise ValueError("text attachments cannot have image dimensions")

    def to_dict(self) -> dict[str, JSONValue]:
        result: dict[str, JSONValue] = {
            "sha256": self.sha256,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "display_name": self.display_name,
        }
        if self.width is not None:
            result["width"] = self.width
            result["height"] = cast(int, self.height)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> AttachmentRef:
        if not isinstance(data, Mapping):
            raise TypeError("attachment must be an object")
        return cls(
            sha256=cast(str, data["sha256"]),
            media_type=cast(str, data["media_type"]),
            size_bytes=cast(int, data["size_bytes"]),
            display_name=cast(str, data["display_name"]),
            width=cast(int | None, data.get("width")),
            height=cast(int | None, data.get("height")),
        )

    def summary(self) -> str:
        dimensions = (
            f" {self.width}x{self.height}" if self.width is not None else ""
        )
        return (
            f"{self.display_name} ({self.media_type}, {self.size_bytes} bytes"
            f"{dimensions}, sha256={self.sha256[:12]})"
        )


def freeze_attachments(values: Sequence[AttachmentRef]) -> tuple[AttachmentRef, ...]:
    attachments = tuple(values)
    if len(attachments) > MAX_MESSAGE_ATTACHMENTS:
        raise ValueError(f"messages support at most {MAX_MESSAGE_ATTACHMENTS} attachments")
    if any(not isinstance(item, AttachmentRef) for item in attachments):
        raise TypeError("attachments must contain only AttachmentRef values")
    if sum(item.size_bytes for item in attachments) > MAX_MESSAGE_ATTACHMENT_BYTES:
        raise ValueError("message attachment bytes exceed the configured limit")
    if len({item.sha256 for item in attachments}) != len(attachments):
        raise ValueError("message attachments must not repeat content")
    return attachments
