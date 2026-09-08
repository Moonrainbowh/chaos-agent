import unittest

from code_agent.interfaces.input_buffer import InputBuffer
from code_agent.interfaces.input_events import MAX_PASTE_BYTES


LONG = "\n".join(["中文 long paste 😀"] * 11)
LABEL = f"[chars: {len(LONG)}]"


class InputAtomTests(unittest.TestCase):
    def test_text_before_after_and_between_pastes_stays_visible(self):
        buffer = InputBuffer()
        buffer.insert("before ")
        buffer.insert_paste(LONG)
        buffer.insert(" compare ")
        buffer.insert_paste(LONG)
        buffer.insert(" please")
        view, cursor = buffer.display
        self.assertEqual(view, f"before {LABEL} compare {LABEL} please")
        self.assertEqual(cursor, len(view))
        self.assertEqual(buffer.submit(), f"before {LONG} compare {LONG} please")

    def test_atom_navigation_and_deletion_preserve_adjacent_text(self):
        buffer = InputBuffer()
        buffer.insert("a")
        buffer.insert_paste(LONG)
        buffer.insert("b")
        buffer.move_left()
        self.assertEqual(buffer.display, ("a" + LABEL + "b", 1 + len(LABEL)))
        buffer.move_left()
        self.assertEqual(buffer.display[1], 1)
        buffer.insert("X")
        buffer.delete()
        self.assertEqual(buffer.text, "aXb")
        self.assertEqual(buffer.display, ("aXb", 2))
        buffer.insert_paste(LONG)
        buffer.backspace()
        self.assertEqual(buffer.text, "aXb")

    def test_history_and_rejected_submission_restore_paste_boundaries(self):
        buffer = InputBuffer()
        buffer.insert_paste(LONG)
        buffer.insert(" suffix")
        original = buffer.submit()
        buffer.replace(original)
        self.assertEqual(buffer.display[0], LABEL + " suffix")
        buffer.clear()
        buffer.previous()
        buffer.insert(" visible")
        self.assertEqual(buffer.display[0], LABEL + " suffix visible")
        self.assertEqual(buffer.text, LONG + " suffix visible")

    def test_ten_line_paste_and_typed_newlines_never_hide(self):
        buffer = InputBuffer()
        text = "\n".join(["line"] * 10)
        buffer.insert_paste(text)
        buffer.insert("\nvisible")
        self.assertEqual(buffer.display[0], text + "\nvisible")

    def test_labels_and_object_characters_typed_by_user_are_literal(self):
        buffer = InputBuffer()
        value = "[image1][chars: 888]\ufffc"
        buffer.insert(value)
        self.assertEqual(buffer.display[0], value)
        self.assertEqual(buffer.submit(), value)

    def test_budget_counts_expanded_body_and_rejects_atomically(self):
        buffer = InputBuffer()
        value = "x" * (MAX_PASTE_BYTES - 10) + "\n" * 10
        buffer.insert_paste(value)
        previous = buffer.display
        with self.assertRaises(ValueError):
            buffer.insert("x")
        self.assertEqual(buffer.display, previous)
        self.assertEqual(buffer.text, value)

    def test_vertical_navigation_snaps_to_atom_boundary(self):
        buffer = InputBuffer()
        buffer.insert_paste(LONG)
        buffer.insert("\nx")
        self.assertTrue(buffer.move_up())
        self.assertEqual(buffer.cursor, 0)
        self.assertTrue(buffer.move_down())
        self.assertEqual(buffer.display[1], len(LABEL) + 1)

    def test_images_are_atomic_and_absent_from_prompt_and_history(self):
        buffer = InputBuffer()
        buffer.sync_images((("id1", "[image1]"), ("id2", "[image2]")))
        buffer.insert(" compare")
        self.assertEqual(buffer.display[0], "[image1][image2] compare")
        self.assertEqual(buffer.text, " compare")
        buffer.move_home()
        self.assertEqual(buffer.delete(), ("id1",))
        self.assertEqual(buffer.display[0], "[image2] compare")
        buffer.submit()
        buffer.previous()
        self.assertEqual(buffer.display[0], " compare")
