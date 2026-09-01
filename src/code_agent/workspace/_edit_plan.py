from __future__ import annotations

from dataclasses import dataclass

from ._text_codec import TextFileFormat, encode_existing_text, encode_new_text


@dataclass(frozen=True)
class EditPlan:
    """Describe one exact text edit and the bytes it will write."""

    relative_path: str
    before_sha256: str | None
    after_text: str
    diff: str
    existed: bool
    after_bytes: bytes | None = None
    text_format: TextFileFormat | None = None

    def __post_init__(self) -> None:
        supplied = self.after_bytes is not None or self.text_format is not None
        if (self.after_bytes is None) != (self.text_format is None):
            raise ValueError("after_bytes and text_format must be supplied together")
        if not supplied:
            encoded = encode_new_text(self.after_text)
            object.__setattr__(self, "after_bytes", encoded.data)
            object.__setattr__(self, "text_format", encoded.format)
            return
        if type(self.after_bytes) is not bytes:
            raise TypeError("after_bytes must be bytes")
        if type(self.text_format) is not TextFileFormat:
            raise TypeError("text_format must be a TextFileFormat")
        encoded = encode_existing_text(self.after_text, self.text_format)
        if (
            encoded.text != self.after_text
            or encoded.data != self.after_bytes
            or encoded.format != self.text_format
        ):
            raise ValueError("after_text, after_bytes, and text_format must agree")
