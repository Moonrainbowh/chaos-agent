"""Decode ConPTY Win32 input envelopes before parsing bracketed paste."""
from collections import deque
import time

from .console_shortcuts import read_character

WIN32_INPUT_ENABLE = "\x1b[?9001h"
WIN32_INPUT_DISABLE = "\x1b[?9001l"
_NAVIGATION = {33: "page_up", 34: "page_down", 35: "end", 36: "home",
               37: "left", 38: "up", 39: "right", 40: "down", 46: "delete"}


class Win32Input:
    def __init__(self, console):
        self.console = console
        self.pending = deque()

    def kbhit(self):
        return bool(self.pending) or self.console.kbhit()

    def _record(self, *, key_mode=False):
        if self.pending:
            return self.pending.popleft()
        character = read_character(self.console) if key_mode else self.console.getwch()
        if character != "\x1b":
            return character
        sequence = character
        deadline = time.monotonic() + .08
        while len(sequence) < 96:
            if not self.console.kbhit():
                if time.monotonic() >= deadline:
                    break
                time.sleep(.001)
                continue
            sequence += self.console.getwch()
            if len(sequence) == 2 and sequence[-1] != "[":
                break
            if len(sequence) > 2 and "@" <= sequence[-1] <= "~":
                break
        if sequence.startswith("\x1b[") and sequence.endswith("_"):
            fields = sequence[2:-1].split(";")
            if len(fields) == 6 and all(not f or f.isdecimal() for f in fields):
                return tuple(int(f or "0") for f in fields)
        self.pending.extend(sequence[1:])
        return character

    def getwch(self):
        while True:
            record = self._record()
            if isinstance(record, str):
                return record
            vk, scan, code, down, modifiers, repeat = record
            # ConPTY represents Alt+numpad Unicode on Alt's key-up record.
            if code and (down or vk == 18):
                return chr(code)

    def read_key_character(self):
        record = self._record(key_mode=True)
        if isinstance(record, str):
            return record
        vk, scan, code, down, modifiers, repeat = record
        if not down and vk != 18:
            return ""
        if vk == 13:
            return "shift+enter" if modifiers & 28 else "enter"
        if vk == 86 and modifiers & 3 and not modifiers & 12:
            return "alt+v"
        if vk == 27:
            return "escape"
        if vk in _NAVIGATION:
            return _NAVIGATION[vk]
        if 0xD800 <= code <= 0xDBFF:
            following = self.getwch()
            if 0xDC00 <= ord(following) <= 0xDFFF:
                return chr(0x10000 + ((code - 0xD800) << 10) + ord(following) - 0xDC00)
            self.pending.appendleft(following)
        return chr(code) if code else ""
