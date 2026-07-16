from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path: sys.path.insert(0, str(SRC_ROOT))

from code_agent.interfaces.profile_control import ProfileControl
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig


def _profile(name: str) -> ModelProfile:
    return ModelProfile(name, ProviderConfig("https://api.example.test", name, ApiProtocol.RESPONSES, "KEY"), 1000, 100)


class ProfileControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_waits_for_async_runtime_replacement(self) -> None:
        applied: list[str] = []
        async def apply(profile: ModelProfile) -> None: applied.append(profile.name)
        control = ProfileControl({"one": _profile("one"), "two": _profile("two")}, "one", apply)

        summary = await control.use("two", idle=True)

        self.assertEqual(summary.name, "two")
        self.assertEqual(applied, ["two"])

    async def test_active_task_cannot_switch(self) -> None:
        async def apply(_: ModelProfile) -> None: return None
        control = ProfileControl({"one": _profile("one")}, "one", apply)
        with self.assertRaises(RuntimeError): await control.use("one", idle=False)
