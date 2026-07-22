from __future__ import annotations

import unittest

from code_agent.interfaces.permission_control import PermissionControl
from code_agent.policy.models import ApprovalMode


class PermissionControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_modes_and_switches_at_idle_boundary(self) -> None:
        applied: list[ApprovalMode] = []

        async def apply(mode: ApprovalMode) -> None:
            applied.append(mode)

        control = PermissionControl(ApprovalMode.UNRESTRICTED, apply)

        self.assertEqual(control.current.name, "unrestricted")
        self.assertEqual(
            tuple(item.name for item in control.list()),
            ("unrestricted", "plan", "ask", "auto", "elevated", "full-local"),
        )

        selected = await control.use("ask", idle=True)

        self.assertEqual(selected.name, "ask")
        self.assertEqual(control.current.name, "ask")
        self.assertEqual(applied, [ApprovalMode.ASK])

    async def test_rejects_running_or_unknown_mode(self) -> None:
        async def apply(_: ApprovalMode) -> None:
            raise AssertionError("invalid selection must not be applied")

        control = PermissionControl(ApprovalMode.UNRESTRICTED, apply)

        with self.assertRaisesRegex(RuntimeError, "only when idle"):
            await control.use("ask", idle=False)
        with self.assertRaisesRegex(ValueError, "unknown permission mode"):
            await control.use("everything", idle=True)

        self.assertEqual(control.current.name, "unrestricted")
