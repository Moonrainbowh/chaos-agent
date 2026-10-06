"""Oversized workspace rules fail through the default application before actions."""
import unittest
from unittest.mock import patch
from code_agent.core.events import EventKind
from code_agent.core.errors import ContextBuildError
from code_agent.context.errors import RuleLimitError
from code_agent.interfaces.runtime_errors import runtime_error_summary
from code_agent.core.tests._engine_support import FakeModelClient
from tests.test_tui_repair_integration import application_fixture


class RuleBudgetIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_application_blocks_model_and_actions_on_oversized_rules(self):
        with application_fixture() as app:
            try:
                root = app.dispatcher.editor.guard.root
                (root / 'AGENTS.md').write_text('Mandatory workspace rule. ' * 1_000, encoding='utf-8')
                marker = root / 'unchanged.txt'
                marker.write_text('original', encoding='utf-8')
                model = FakeModelClient([])
                engine = app.controller._engine
                engine._model = model
                events = []
                with patch.object(engine._actions, 'dispatch', wraps=engine._actions.dispatch) as dispatch:
                    with self.assertRaises(ContextBuildError) as caught:
                        async for event in engine.run('Update unchanged.txt'):
                            events.append(event)
                    dispatch.assert_not_called()
                self.assertEqual(model.calls, [])
                self.assertIsInstance(caught.exception.__cause__, RuleLimitError)
                self.assertIn('AGENTS.md', str(caught.exception.__cause__))
                visible = runtime_error_summary(caught.exception)
                self.assertIn('AGENTS.md', visible)
                self.assertIn('rendered_tokens=', visible)
                self.assertIn('max_rule_tokens=', visible)
                self.assertNotIn('Mandatory workspace rule', visible)
                self.assertFalse(any(event.kind in (EventKind.MODEL_STARTED, EventKind.ACTION_STARTED)
                                     for event in events))
                self.assertEqual(marker.read_text(encoding='utf-8'), 'original')
            finally:
                await app.aclose()
