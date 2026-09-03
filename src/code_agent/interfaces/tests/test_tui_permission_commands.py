from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from code_agent.interfaces.permission_control import PermissionControl
from code_agent.interfaces.tui_permission_commands import handle_permission_command
from code_agent.policy.command_rules import ProcessRuleStore
from code_agent.policy.models import ApprovalMode


class _App:
    def __init__(self, permissions: PermissionControl) -> None:
        self.permissions = permissions
        self._run_task = None
        self.entries: list[tuple[object, str]] = []

    def _append(self, kind: object, text: str) -> None:
        self.entries.append((kind, text))


class TuiPermissionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_allow_list_and_revoke_exact_process_rule(self) -> None:
        async def apply(_: ApprovalMode) -> None:
            pass

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            permissions = PermissionControl(
                ApprovalMode.AUTO,
                apply,
                rules=ProcessRuleStore(root / "rules.sqlite3"),
                workspace_root=root,
                workspace_fingerprint="workspace-1",
            )
            app = _App(permissions)

            allowed = await handle_permission_command(
                app,
                f'允许命令 "{sys.executable}" -V',
                "允许命令",
            )
            listed = await handle_permission_command(app, "规则", "规则")
            rule_id = permissions.list_rules()[0].id
            revoked = await handle_permission_command(
                app, f"撤销 {rule_id[:8]}", "撤销"
            )

            self.assertTrue(allowed)
            self.assertTrue(listed)
            self.assertTrue(revoked)
            self.assertEqual(permissions.list_rules(), ())
            self.assertIn("permanent command allowed", app.entries[0][1])
            self.assertIn(rule_id[:8], app.entries[1][1])


if __name__ == "__main__":
    unittest.main()
