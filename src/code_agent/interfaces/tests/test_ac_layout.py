import re
import unittest

from code_agent.interfaces.terminal_ac_layout import render_ac_rows
from code_agent.interfaces.terminal_display import DisplayKind, display_width, text_entry
from code_agent.interfaces.terminal_renderer import render_entry, ColorMode, Theme


def plain(value):
    return re.sub(r"\x1b\[[0-9;]*m", "", value)


class AcLayoutTests(unittest.TestCase):
    def test_short_labels_align_with_first_body_row_and_sections_have_rules(self):
        rows = render_ac_rows("## 核心建议\n\n先看结论。\n\n## 代码逻辑\n正文", 70, ColorMode.NEVER)
        self.assertEqual(rows[0], "一、核心建议" + " " * 2 + "先看结论。")
        self.assertEqual(rows.count("─" * 70), 1)

    def test_stream_and_final_share_layout_and_never_leak_wrapped_bold(self):
        text = "## 建议\n**" + "跨行重点" * 20 + "**\n\n## 下一步\n完成检查"
        for width in (20, 52, 80, 120):
            final = render_ac_rows(text, width, ColorMode.ALWAYS)
            self.assertEqual(final, render_ac_rows(text, width, ColorMode.ALWAYS, incomplete=True))
            self.assertNotIn("**", plain("\n".join(final)))
            self.assertTrue(all(display_width(plain(row)) <= width for row in final))

    def test_nested_lists_and_code_blanks_are_preserved(self):
        text = "## 逻辑\n- parent\n  - child\n```python\nx = 1\n\n\ny = 2\n```"
        rows = render_ac_rows(text, 80, ColorMode.NEVER)
        self.assertIn(" " * 16 + "- child", rows)
        self.assertIn(" " * 16 + "x = 1", rows)
        self.assertEqual(rows.count(" " * 16), 2)

    def test_long_heading_wraps_in_left_column_without_truncation(self):
        text = "这是一个不能截断的完整章节标题"
        rows = render_ac_rows("## " + text + "\n正文", 60, ColorMode.NEVER)
        self.assertEqual(rows[0], "一、这是一个" + " " * 2 + "正文")
        self.assertEqual("".join(row.split("  ")[0] for row in rows), "一、" + text)
        self.assertTrue(all(display_width(row) <= 60 for row in rows))

    def test_mixed_heading_lengths_share_body_column_and_narrow_layout(self):
        titles = ["核心功能", "当前进度", "安全与数据处理", "尚未完全验收或支持", "开发和验证"]
        value = "\n".join(f"## {title}\nBODY{i}\n继续正文" for i, title in enumerate(titles))
        for color in (ColorMode.ALWAYS, ColorMode.NEVER):
            rows = render_ac_rows(value, 80, color)
            self.assertEqual(rows, render_ac_rows(value, 80, color, incomplete=True))
            for row in rows:
                visible = plain(row)
                if "BODY" in visible:
                    self.assertEqual(display_width(visible.split("BODY")[0]), 14)
            self.assertEqual(sum("BODY" in row for row in rows), 5)
        narrow = render_ac_rows(value, 40, ColorMode.NEVER)
        self.assertTrue(all(f"BODY{i}" in narrow for i in range(5)))

    def test_complete_answer_uses_ac_in_slate_only(self):
        text = "## 建议\n内容\n## 注意\n边界"
        output = render_entry(text_entry(DisplayKind.AGENT, text), 80, theme=Theme.SLATE, color=ColorMode.NEVER)
        self.assertIn("一、建议" + " " * 6 + "内容", output)
        self.assertIn("─" * 78, output)

    def test_table_and_unclosed_stream_emphasis_are_safe(self):
        text = "## 对照\n| 列一 | 列二 |\n| --- | --- |\n| 值一 | 值二 |\n## 结论\n**仍在生成"
        rows = render_ac_rows(text, 60, ColorMode.NEVER, incomplete=True)
        self.assertIn("值一", "\n".join(rows))
        self.assertNotIn("**", "\n".join(rows))
        self.assertTrue(all(display_width(row) <= 60 for row in rows))

    def test_chinese_heading_numbers_and_existing_prefix(self):
        value = "## 一、已有标题\n正文\n## 新标题\n正文\n## **三、强调标题**\n正文"
        rows = "\n".join(render_ac_rows(value, 80, ColorMode.NEVER))
        self.assertIn("一、已有标题", rows)
        self.assertIn("二、新标题", rows)
        self.assertNotIn("一、一、", rows)
        self.assertNotIn("三、三、", rows)
        self.assertIn("三、强调标题", rows)

    def test_numbering_beyond_nine_and_resets_per_answer(self):
        value = "\n".join("## 标题\n正文" for _ in range(12))
        rows = "\n".join(render_ac_rows(value, 80, ColorMode.NEVER))
        self.assertIn("十、标题", rows)
        self.assertIn("十一、标题", rows)
        self.assertIn("十二、标题", rows)
        self.assertTrue(render_ac_rows("## 标题\n正文", 80, ColorMode.NEVER)[0].startswith("一、"))
