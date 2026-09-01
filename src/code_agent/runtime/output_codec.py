from __future__ import annotations

import base64
import codecs
import ctypes
import os
from dataclasses import dataclass
from enum import Enum


class OutputEncoding(str, Enum):
    UTF_8 = "utf-8"
    WINDOWS_ANSI = "windows-ansi"
    WINDOWS_OEM = "windows-oem"
    UTF_16_LE = "utf-16-le"
    UTF_16_BE = "utf-16-be"


class OutputDecodeStatus(str, Enum):
    DECODED = "decoded"
    UNKNOWN_OR_MIXED = "unknown_or_mixed"
    INCOMPLETE_TAIL = "incomplete_tail"


@dataclass(frozen=True)
class DecodedOutput:
    text: str | None
    encoding: OutputEncoding
    status: OutputDecodeStatus
    base64_data: str | None = None
    code_page: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.encoding, OutputEncoding):
            raise TypeError("encoding must be an OutputEncoding")
        if not isinstance(self.status, OutputDecodeStatus):
            raise TypeError("status must be an OutputDecodeStatus")
        if (self.status is OutputDecodeStatus.DECODED) != (self.text is not None):
            raise ValueError("decoded output requires text and failed output forbids it")
        if (self.status is OutputDecodeStatus.DECODED) == (
            self.base64_data is not None
        ):
            raise ValueError("base64 data is required only for failed decoding")
        if self.code_page is not None and (
            isinstance(self.code_page, bool)
            or not isinstance(self.code_page, int)
            or self.code_page <= 0
        ):
            raise ValueError("code_page must be a positive integer or None")


def decode_output(
    data: bytes | bytearray | memoryview,
    encoding: OutputEncoding = OutputEncoding.UTF_8,
    *,
    truncated: bool = False,
) -> DecodedOutput:
    """Strictly decode one captured stream without discarding original bytes."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    if not isinstance(encoding, OutputEncoding):
        raise TypeError("encoding must be an OutputEncoding")
    if not isinstance(truncated, bool):
        raise TypeError("truncated must be a bool")

    raw = bytes(data)
    selected, payload, unsupported_bom = _bom_encoding(raw, encoding)
    if unsupported_bom:
        return _failed(raw, selected, OutputDecodeStatus.UNKNOWN_OR_MIXED)
    code_page: int | None = None
    try:
        codec, code_page = _codec_name(selected)
        text = payload.decode(codec, errors="strict")
    except UnicodeDecodeError:
        status = (
            OutputDecodeStatus.INCOMPLETE_TAIL
            if truncated and _has_incomplete_tail(payload, codec)
            else OutputDecodeStatus.UNKNOWN_OR_MIXED
        )
        return _failed(raw, selected, status, code_page=code_page)
    except (LookupError, OSError):
        return _failed(
            raw,
            selected,
            OutputDecodeStatus.UNKNOWN_OR_MIXED,
            code_page=code_page,
        )
    return DecodedOutput(text, selected, OutputDecodeStatus.DECODED, code_page=code_page)


def _failed(
    raw: bytes,
    encoding: OutputEncoding,
    status: OutputDecodeStatus,
    *,
    code_page: int | None = None,
) -> DecodedOutput:
    encoded = base64.b64encode(raw).decode("ascii")
    return DecodedOutput(
        None,
        encoding,
        status,
        base64_data=encoded,
        code_page=code_page,
    )


def _bom_encoding(
    raw: bytes, requested: OutputEncoding
) -> tuple[OutputEncoding, bytes, bool]:
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return requested, raw, True
    if raw.startswith(codecs.BOM_UTF8):
        return OutputEncoding.UTF_8, raw[len(codecs.BOM_UTF8) :], False
    if raw.startswith(codecs.BOM_UTF16_LE):
        return OutputEncoding.UTF_16_LE, raw[len(codecs.BOM_UTF16_LE) :], False
    if raw.startswith(codecs.BOM_UTF16_BE):
        return OutputEncoding.UTF_16_BE, raw[len(codecs.BOM_UTF16_BE) :], False
    return requested, raw, False


def _codec_name(encoding: OutputEncoding) -> tuple[str, int | None]:
    if encoding is OutputEncoding.WINDOWS_ANSI:
        page = _windows_code_page(oem=False)
        return f"cp{page}", page
    if encoding is OutputEncoding.WINDOWS_OEM:
        page = _windows_code_page(oem=True)
        return f"cp{page}", page
    return encoding.value, None


def _has_incomplete_tail(payload: bytes, codec: str) -> bool:
    """Return true only when non-final decoding succeeds but final flush fails."""
    try:
        decoder = codecs.getincrementaldecoder(codec)(errors="strict")
        decoder.decode(payload, final=False)
    except (LookupError, UnicodeDecodeError):
        return False
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return True
    return False


def _windows_code_page(*, oem: bool) -> int:
    if os.name != "nt":
        raise OSError("Windows code pages are unavailable on this platform")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetOEMCP if oem else kernel32.GetACP
    function.argtypes = ()
    function.restype = ctypes.c_uint32
    value = int(function())
    if value <= 0:
        raise OSError("Windows returned an invalid code page")
    return value
