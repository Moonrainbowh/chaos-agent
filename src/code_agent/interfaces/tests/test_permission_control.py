from __future__ import annotations

import unittest
import sys
import tempfile
from pathlib import Path

from code_agent.interfaces.permission_control import PermissionControl
from code_agent.policy.command_rules import ProcessRuleStore
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

    async def test_manages_workspace_scoped_permanent_process_rules(self) -> None:
        async def apply(_: ApprovalMode) -> None:
            pass

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            control = PermissionControl(
                ApprovalMode.AUTO,
                apply,
                rules=ProcessRuleStore(root / "permissions.sqlite3"),
                workspace_root=root,
                workspace_fingerprint="workspace-1",
            )

            allowed = control.allow_process(sys.executable, ("-V",))
            self.assertEqual(control.list_rules(), (allowed,))
            revoked = control.revoke_rule(allowed.id[:8])

            self.assertEqual(revoked.id, allowed.id)
            self.assertEqual(control.list_rules(), ())
