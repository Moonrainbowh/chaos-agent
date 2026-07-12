from __future__ import annotations

import unittest

from code_agent.interfaces.i18n import Language, localize_task_status, select_language, select_runtime_language, ZH_CN


class I18nTests(unittest.TestCase):
    def test_selects_chinese_windows_default_and_explicit_english(self) -> None:
        self.assertEqual(select_language("zh_CN", None), Language.ZH_CN)
        self.assertEqual(select_language("en_US", None), Language.EN_US)
        self.assertEqual(select_language("zh_CN", "en"), Language.EN_US)

    def test_environment_override_has_precedence_over_system_locale(self) -> None:
        self.assertEqual(
            select_runtime_language({"CODE_AGENT_LANGUAGE": "en"}),
            Language.EN_US,
        )
        self.assertEqual(localize_task_status("waiting_decision", ZH_CN), "等待决定")
