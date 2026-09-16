from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from code_agent.verification.planner import VerificationPlanner


class NeighborTests(unittest.TestCase):
    def test_direct_match_does_not_hide_behavior_groups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('feature/tests/test_decoder.py', 'feature/tests/test_decoder_modifiers.py',
                         'tests/test_decoder_protocol.py', 'tests/test_unrelated.py'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('')
            actual = VerificationPlanner(root).find_impacted_tests(('feature/decoder.py',))
            self.assertEqual(actual, (
                'feature/tests/test_decoder.py', 'feature/tests/test_decoder_modifiers.py',
                'tests/test_decoder_protocol.py',
            ))
