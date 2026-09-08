import unittest
from collections import deque
from unittest.mock import patch

from code_agent.interfaces.console_shortcuts import _InputRecord, _alt_v_prefix
from code_agent.interfaces.terminal_io import read_key


def event(vk, modifiers=0, down=True):
    record = _InputRecord()
    record.kind = 1
    record.data.key.down = down
    record.data.key.virtual_key = vk
    record.data.key.modifiers = modifiers
    return record


class ConsoleShortcutTests(unittest.TestCase):
    def test_alt_v_preserves_modifier_from_real_key_event_shape(self):
        # Captured ConPTY event shape: bare Alt, then V with LEFT_ALT_PRESSED.
        self.assertEqual(_alt_v_prefix([event(0x12, 2), event(0x56, 2)]), 2)
        self.assertEqual(_alt_v_prefix([event(0x56, 1)]), 1)

    def test_ordinary_v_and_altgr_are_not_image_shortcuts(self):
        for modifiers in (0, 4, 8, 6, 9):
            self.assertEqual(_alt_v_prefix([event(0x56, modifiers)]), 0)

    def test_preceding_text_must_not_be_consumed_with_shortcut(self):
        self.assertEqual(_alt_v_prefix([event(0x41), event(0x56, 2)]), 0)

    def test_shortcut_inside_text_burst_is_queued_as_key_not_text(self):
        keys = deque(("a", "alt+v"))

        class Console:
            def kbhit(self):
                return bool(keys)

        with patch.dict("sys.modules", {"msvcrt": Console()}), patch(
            "code_agent.interfaces.terminal_io.read_character", side_effect=lambda _: keys.popleft()
        ):
            self.assertEqual(read_key(), "a")
            self.assertEqual(read_key(), "alt+v")
