import re
import unittest
from unittest.mock import patch

from code_agent.interfaces.terminal_display import DisplayKind, display_width, text_entry
from code_agent.interfaces.terminal_renderer import ColorMode, Theme, render_entry
from code_agent.interfaces.terminal_markdown import render_streaming_markdown_rows


def plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class ReadabilityTests(unittest.TestCase):
    def test_user_background_covers_wrapped_and_empty_rows_without_leaking(self):
        background = "\x1b[48;2;30;48;76m"
        for width in (12, 60, 120):
            result = render_entry(text_entry(DisplayKind.USER, "中文输入" * 8 + "\n\nnext"),
                                  width, theme=Theme.SLATE, color=ColorMode.ALWAYS)
            for row in result.splitlines():
                self.assertTrue(row.startswith(background))
                self.assertTrue(row.endswith("\x1b[0m"))
                self.assertEqual(display_width(plain(row)), width)
            reply = render_entry(text_entry(DisplayKind.AGENT, "reply"), width,
                                 theme=Theme.SLATE, color=ColorMode.ALWAYS)
            self.assertNotIn(background, reply)

    def test_no_color_has_no_background_or_padding(self):
        with patch.dict("os.environ", {"NO_COLOR": "1"}):
            for color in (ColorMode.AUTO, ColorMode.NEVER):
                result = render_entry(text_entry(DisplayKind.USER, "hello"), 60,
                                      theme=Theme.SLATE, color=color)
                self.assertEqual(result, "› hello")

    def test_attachment_summary_remains_inside_user_background(self):
        result = render_entry(text_entry(DisplayKind.USER, "question\nAttachments:\n- image.png"),
                              60, theme=Theme.SLATE, color=ColorMode.ALWAYS)
        rows = result.splitlines()
        self.assertIn("38;2;115;132;156m", rows[-1])
        self.assertTrue(all(row.startswith("\x1b[48;2;30;48;76m") for row in rows))

    def test_long_quote_emphasis_is_parsed_before_wrapping(self):
        value = "> **" + "重要结论" * 20 + "**"
        result = render_entry(text_entry(DisplayKind.AGENT, value), 20,
                              theme=Theme.SLATE, color=ColorMode.ALWAYS)
        self.assertNotIn("**", plain(result))
        self.assertIn("重要结论", plain(result))
        self.assertTrue(all(display_width(row) <= 20 for row in plain(result).splitlines()))

    def test_streaming_collapses_blank_paragraphs_but_keeps_code_blanks(self):
        rows = render_streaming_markdown_rows("one\n\n\n## Two\ntext", 60, ColorMode.NEVER)
        self.assertEqual(rows, ["one", "", "Two", "text"])
        rows = render_streaming_markdown_rows("```\na\n\n\nb\n```", 60, ColorMode.NEVER)
        self.assertIn(["  a", "  ", "  ", "  b"], [rows[1:5]])
