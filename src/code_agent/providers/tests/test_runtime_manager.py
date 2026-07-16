from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.providers.runtime_manager import ProviderRuntime, ProviderRuntimeManager


class _Client:
    def __init__(self) -> None: self.closed = False
    async def aclose(self) -> None: self.closed = True


def _profile(name: str) -> ModelProfile:
    return ModelProfile(name, ProviderConfig("https://api.example.test", name, ApiProtocol.RESPONSES, "KEY"), 1000, 100)


class ProviderRuntimeManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_replaces_then_closes_previous(self) -> None:
        old, new, replaced = _Client(), _Client(), []
        first = ProviderRuntime(_profile("one"), old, object())
        async def build(profile: ModelProfile) -> ProviderRuntime: return ProviderRuntime(profile, new, "runner-two")
        manager = ProviderRuntimeManager({"one": first.profile, "two": _profile("two")}, first, build, replaced.append)

        current = await manager.switch("two", idle=True)

        self.assertEqual(current.runner, "runner-two")
        self.assertEqual(replaced, ["runner-two"])
        self.assertTrue(old.closed)
        self.assertFalse(new.closed)

    async def test_failed_build_or_active_task_preserves_current_runtime(self) -> None:
        old = _Client(); first = ProviderRuntime(_profile("one"), old, object())
        async def broken(_: ModelProfile) -> ProviderRuntime: raise RuntimeError("build failed")
        manager = ProviderRuntimeManager({"one": first.profile, "two": _profile("two")}, first, broken, lambda _: None)

        with self.assertRaises(RuntimeError): await manager.switch("two", idle=False)
        with self.assertRaisesRegex(RuntimeError, "build failed"): await manager.switch("two", idle=True)
        self.assertIs(manager.current, first)
        self.assertFalse(old.closed)
