import contextlib
import io
import unittest

from code_agent.core.errors import ModelStreamError
from code_agent.core.tests import test_engine_limits as fixtures
from code_agent.core.tests._engine_support import MemorySessionRepository


class ModelErrorOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_error_keeps_cause_without_printing_or_copying_body(self):
        class BrokenModel:
            async def stream(self, *args, **kwargs):
                raise RuntimeError("<!DOCTYPE html> private upstream page")
                yield
        engine = fixtures.AgentEngineLimitTests().make_engine(BrokenModel(), MemorySessionRepository())
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(ModelStreamError) as caught:
            _ = [event async for event in engine.run("inspect")]
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(str(caught.exception), "model stream failed")
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
