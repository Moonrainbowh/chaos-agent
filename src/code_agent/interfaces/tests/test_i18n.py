from __future__ import annotations

import unittest

from code_agent.interfaces.i18n import Language, select_language


class I18nTests(unittest.TestCase):
    def test_selects_chinese_windows_default_and_explicit_english(self) -> None:
        self.assertEqual(select_language("zh_CN", None), Language.ZH_CN)
        self.assertEqual(select_language("en_US", None), Language.EN_US)
        self.assertEqual(select_language("zh_CN", "en"), Language.EN_US)
