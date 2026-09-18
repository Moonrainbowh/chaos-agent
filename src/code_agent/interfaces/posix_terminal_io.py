"""Raw POSIX terminal input for the interactive terminal UI."""
from __future__ import annotations

import codecs
import os
import select
import sys
from collections import deque

from .input_events import MAX_PASTE_BYTES


_VT_KEYS = {
    "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
    "\x1b[H": "home", "\x1b[F": "end", "\x1b[1~": "home", "\x1b[4~": "end",
    "\x1b[3~": "delete", "\x1b[5~": "page_up", "\x1b[6~": "page_down",
    "\x1bOA": "up", "\x1bOB": "down", "\x1bOC": "right", "\x1bOD": "left",
    "\x1bOH": "home", "\x1bOF": "end",
}
_PASTE_START = "\x1b[200~"
_PASTE_END = "\x1b[201~"
_decoder = codecs.getincrementaldecoder("utf-8")("replace")
_pending: deque[str] = deque()


def read_key(*, timeout: float | None = None) -> str | None:
    """Read one key or bracketed paste event from a raw POSIX terminal."""
    key = _pending.popleft() if _pending else _read_character(timeout)
    if key is None:
        return None
    if key == "\x1b":
        return _read_escape_sequence()
    return "\x08" if key == "\x7f" else key


def _read_escape_sequence() -> str:
    suffix = _read_character(.08)
    if suffix is None:
        return "\x1b"
    if suffix in {"v", "V"}:
        return "alt+v"
    if suffix == "O":
        return _VT_KEYS.get("\x1bO" + (_read_character(.08) or ""), "")
    if suffix != "[":
        _pending.append(suffix)
        return "\x1b"
    sequence = "\x1b["
    while len(sequence) < 32:
        character = _read_character(.08)
        if character is None:
            break
        sequence += character
        if "@" <= character <= "~":
            break
    if sequence == _PASTE_START:
        return _read_bracketed_paste()
    return _VT_KEYS.get(sequence, "") or _enter_sequence(sequence)


def _read_character(timeout: float | None) -> str | None:
    """Return one UTF-8 character from raw stdin, honouring an optional timeout."""
    descriptor = sys.stdin.fileno()
    ready, _, _ = select.select((descriptor,), (), (), timeout)
    if not ready:
        return None
    while True:
        chunk = os.read(descriptor, 1)
        if not chunk:
            return None
        character = _decoder.decode(chunk)
        if character:
            return character


def _read_bracketed_paste() -> str:
    chars: list[str] = []
    suffix = ""
    overflow = False
    while True:
        character = _read_character(None)
        if character is None:
            return _PASTE_START + "".join(chars) + _PASTE_END
        suffix += character
        if suffix == _PASTE_END:
            value = "x" * (MAX_PASTE_BYTES + 1) if overflow else "".join(chars)
            return _PASTE_START + value + _PASTE_END
        while suffix and not _PASTE_END.startswith(suffix):
            if len(chars) < MAX_PASTE_BYTES + 1:
                chars.append(suffix[0])
            else:
                overflow = True
            suffix = suffix[1:]


def _enter_sequence(sequence: str) -> str:
    if sequence.endswith("u") and sequence.startswith("\x1b["):
        fields = sequence[2:-1].split(";")
        if fields[0].split(":", 1)[0] != "13":
            return ""
        modifier = fields[1].split(":", 1)[0] if len(fields) > 1 else "1"
        flags = max(0, int(modifier) - 1) if modifier.isdecimal() else 0
        return "shift+enter" if flags & 5 else "\r"
    return ""
