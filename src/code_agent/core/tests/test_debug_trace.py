import json
import logging
import unittest
from io import StringIO

from code_agent.core.debug_trace import enable_trace, trace_event


class DebugTraceTests(unittest.TestCase):
    def test_trace_is_json_and_bounds_untrusted_values(self) -> None:
        logger = logging.getLogger("code_agent.runtime_trace")
        enable_trace()
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        previous = logger.level
        logger.setLevel(logging.INFO)
        try:
            trace_event(
                "context.build", "completed", prompt="secret " + "x" * 500,
                paths=["a.py", "b.py"],
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        record = json.loads(stream.getvalue())
        self.assertEqual(record["stage"], "context.build")
        self.assertLessEqual(len(record["prompt"]), 160)
        self.assertEqual(record["paths"], ["a.py", "b.py"])
