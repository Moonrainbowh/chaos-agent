from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.sessions._workspace_codec import decode_json  # noqa: E402
from code_agent.sessions._workspace_model_values import MAX_JSON_BYTES  # noqa: E402
from code_agent.sessions.errors import SessionCorruptionError  # noqa: E402


class WorkspaceCodecLimitTests(unittest.TestCase):
    def test_oversized_utf8_payload_is_rejected_before_parsing(self) -> None:
        payload = json.dumps("x" * MAX_JSON_BYTES)
        with patch("code_agent.sessions._workspace_codec.json.loads") as loads:
            with self.assertRaises(SessionCorruptionError):
                decode_json(payload, "cursor")
        loads.assert_not_called()

    def test_parser_resource_and_unicode_failures_are_wrapped(self) -> None:
        with patch(
            "code_agent.sessions._workspace_codec.json.loads", side_effect=MemoryError
        ):
            with self.assertRaises(SessionCorruptionError):
                decode_json("{}", "cursor")
        with self.assertRaises(SessionCorruptionError):
            decode_json('"\\ud800"', "cursor")

    def test_parsed_depth_count_and_json_safety_are_bounded(self) -> None:
        deep = '{"a":' * 20 + "0" + "}" * 20
        too_many = json.dumps([0] * 10_001)
        for payload in (deep, too_many, "NaN"):
            with self.subTest(payload=payload[:20]):
                with self.assertRaises(SessionCorruptionError):
                    decode_json(payload, "cursor")


if __name__ == "__main__":
    unittest.main()
