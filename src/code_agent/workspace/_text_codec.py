from __future__ import annotations

import codecs
import ctypes
import os
from dataclasses import dataclass, replace


AUTO_ENCODING = "auto"
WINDOWS_ANSI = "windows-ansi"
WINDOWS_OEM = "windows-oem"
SUPPORTED_REQUESTS = frozenset((AUTO_ENCODING, WINDOWS_ANSI, WINDOWS_OEM))
NEWLINE_STYLES = frozenset(("none", "lf", "crlf", "cr", "mixed"))

_UNICODE_CODECS = frozenset(
    ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be")
)
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


class TextCodecError(ValueError):
    """A text encoding cannot be identified or represented without loss."""


@dataclass(frozen=True)
class TextFileFormat:
    """Describe the byte encoding and line endings of one decoded file."""

    encoding: str
    bom: bool
    newline: str
    code_page: int | None = None

    def __post_init__(self) -> None:
        if self.encoding not in _UNICODE_CODECS | {WINDOWS_ANSI, WINDOWS_OEM}:
            raise ValueError("unsupported text format encoding")
        if type(self.bom) is not bool:
            raise TypeError("bom must be a boolean")
        if self.newline not in NEWLINE_STYLES:
            raise ValueError("unsupported newline style")
        if self.encoding in _UNICODE_CODECS:
            if self.code_page is not None:
                raise ValueError("Unicode formats cannot declare a code page")
        elif (
            type(self.code_page) is not int
            or self.code_page <= 0
            or self.bom
        ):
            raise ValueError("Windows formats require a positive code page and no BOM")


@dataclass(frozen=True)
class DecodedText:
    text: str
    format: TextFileFormat


@dataclass(frozen=True)
class EncodedText:
    text: str
    data: bytes
    format: TextFileFormat


def decode_text_bytes(data: bytes, encoding: str = AUTO_ENCODING) -> DecodedText:
    """Decode exact bytes using a BOM, strict UTF-8, or an explicit Windows page."""
    _require_bytes(data)
    request = _require_request(encoding)
    detected = _detect_bom(data) if request == AUTO_ENCODING else None
    if detected is not None:
        prefix, codec = detected
        payload = data[len(prefix) :]
        text = _strict_decode(payload, codec)
        text_format = TextFileFormat(codec, True, detect_newline(text))
    elif request == AUTO_ENCODING:
        payload, codec = data, "utf-8"
        text = _strict_decode(payload, codec)
        text_format = TextFileFormat(codec, False, detect_newline(text))
    else:
        code_page, codec = _windows_code_page(request)
        payload = data
        text = _strict_decode(payload, codec)
        text_format = TextFileFormat(
            request, False, detect_newline(text), code_page
        )
    if "\0" in text:
        raise TextCodecError("decoded text contains a NUL character")
    if _strict_encode(text, codec) != payload:
        raise TextCodecError("text encoding does not round-trip exactly")
    return DecodedText(text, text_format)


def encode_existing_text(text: str, source: TextFileFormat) -> EncodedText:
    """Encode an edit while preserving a known encoding, BOM, and stable EOL style."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(source, TextFileFormat):
        raise TypeError("source must be a TextFileFormat")
    normalized = preserve_newline_style(text, source.newline)
    return _encode(normalized, source)


def encode_new_text(text: str, encoding: str = AUTO_ENCODING) -> EncodedText:
    """Encode a new file; auto means UTF-8 without a BOM."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    request = _require_request(encoding)
    if request == AUTO_ENCODING:
        text_format = TextFileFormat("utf-8", False, detect_newline(text))
    else:
        code_page, _codec = _windows_code_page(request)
        text_format = TextFileFormat(
            request, False, detect_newline(text), code_page
        )
    return _encode(text, text_format)


def detect_newline(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    crlf = text.count("\r\n")
    without_crlf = text.replace("\r\n", "")
    styles = {
        style
        for style, count in (
            ("crlf", crlf),
            ("cr", without_crlf.count("\r")),
            ("lf", without_crlf.count("\n")),
        )
        if count
    }
    if not styles:
        return "none"
    if len(styles) > 1:
        return "mixed"
    return next(iter(styles))


def preserve_newline_style(text: str, newline: str) -> str:
    """Normalize inserted text only when the source had one consistent EOL style."""
    if newline not in NEWLINE_STYLES:
        raise ValueError("unsupported newline style")
    if newline not in {"lf", "crlf", "cr"}:
        return text
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline == "crlf":
        return normalized.replace("\n", "\r\n")
    if newline == "cr":
        return normalized.replace("\n", "\r")
    return normalized


def _encode(text: str, text_format: TextFileFormat) -> EncodedText:
    codec = _format_codec(text_format)
    payload = _strict_encode(text, codec)
    if _strict_decode(payload, codec) != text:
        raise TextCodecError("encoded text does not round-trip exactly")
    prefix = _bom_for(text_format) if text_format.bom else b""
    actual = replace(text_format, newline=detect_newline(text))
    return EncodedText(text, prefix + payload, actual)


def _strict_decode(data: bytes, codec: str) -> str:
    try:
        return data.decode(codec, errors="strict")
    except (LookupError, UnicodeDecodeError) as error:
        raise TextCodecError(f"bytes are not valid {codec}") from error


def _strict_encode(text: str, codec: str) -> bytes:
    try:
        return text.encode(codec, errors="strict")
    except (LookupError, UnicodeEncodeError) as error:
        raise TextCodecError(f"text is not representable as {codec}") from error


def _detect_bom(data: bytes) -> tuple[bytes, str] | None:
    return next(((bom, codec) for bom, codec in _BOMS if data.startswith(bom)), None)


def _bom_for(text_format: TextFileFormat) -> bytes:
    for bom, codec in _BOMS:
        if codec == text_format.encoding:
            return bom
    raise TextCodecError("Windows code pages cannot carry a Unicode BOM")


def _format_codec(text_format: TextFileFormat) -> str:
    if text_format.encoding in _UNICODE_CODECS:
        return text_format.encoding
    assert text_format.code_page is not None
    return f"cp{text_format.code_page}"


def _windows_code_page(request: str) -> tuple[int, str]:
    if os.name != "nt":
        raise TextCodecError(f"{request} is available only on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    name = "GetACP" if request == WINDOWS_ANSI else "GetOEMCP"
    function = getattr(kernel32, name)
    function.argtypes = ()
    function.restype = ctypes.c_uint
    code_page = int(function())
    if code_page <= 0:
        raise TextCodecError(f"Windows returned an invalid {request} code page")
    codec = f"cp{code_page}"
    try:
        codecs.lookup(codec)
    except LookupError as error:
        raise TextCodecError(f"Python does not support Windows code page {code_page}") from error
    return code_page, codec


def _require_request(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("encoding must be text")
    normalized = value.strip().casefold()
    if normalized not in SUPPORTED_REQUESTS:
        raise TextCodecError(
            "encoding must be auto, windows-ansi, or windows-oem"
        )
    return normalized


def _require_bytes(value: bytes) -> None:
    if not isinstance(value, bytes):
        raise TypeError("data must be bytes")
