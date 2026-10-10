"""Actual Provider preparation/transport, synthetic credentials and offline HTTP."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from semantic_harness import ROOT, S16
from run_semantic_experiment import execute

sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(S16)]
import httpx
from code_agent.providers.config import ProviderConfig, ApiProtocol
import run_real_source_completion


class RunnerTests(unittest.TestCase):
    def test_wire_audit_usage_and_isolation_through_real_provider(self):
        calls = []
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                item = {"choices": [{"delta": {"content": "{}"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12,
                                  "completion_tokens_details": {"reasoning_tokens": 2}}}
                yield ("data: " + json.dumps(item) + "\n\ndata: [DONE]\n\n").encode()
        async def fake_send(client, request, *args, **kwargs):
            calls.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=Stream(), request=request)
        provider = ProviderConfig(base_url="https://offline.invalid", model="glm-5.3-flash",
                                  api=ApiProtocol.CHAT_COMPLETIONS,
                                  api_key_env="S16_SYNTHETIC_TEST_KEY")
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"S16_SYNTHETIC_TEST_KEY": "synthetic-test-key-not-live"}), \
             patch.object(run_real_source_completion, "selected_profile", return_value=SimpleNamespace(provider=provider)), \
             patch.object(httpx.AsyncClient, "send", fake_send), \
             contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "run"
            asyncio.run(execute(output))
            self.assertEqual(len(calls), 12)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["external_sends"], 12)
            audit = json.loads((output / "copy_faulty-B/wire-audit-1.json").read_text())
            self.assertFalse(audit["advisory_exposed"])
            payload = json.loads(calls[2]["messages"][1]["content"])
            self.assertNotIn("child_advisory", payload)
            evidence = json.loads((output / "copy_faulty-B/events-1.json").read_text())
            self.assertEqual(evidence["provider_usage"][0]["completion_tokens_details"]["reasoning_tokens"], 2)
            for path in output.rglob("*"):
                if path.is_file():
                    text = path.read_text(encoding="utf-8")
                    self.assertNotIn("synthetic-test-key-not-live", text)
                    self.assertNotIn("https://offline.invalid", text)


if __name__ == "__main__":
    unittest.main()
