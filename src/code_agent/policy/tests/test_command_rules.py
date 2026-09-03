from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from code_agent.core.models import ActionRequest
from code_agent.policy.command_rules import ProcessRuleStore


class ProcessRuleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.store = ProcessRuleStore(self.base / "rules.sqlite3")
        self.fingerprint = "workspace-fingerprint"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, *args: str, cwd: str = ".") -> ActionRequest:
        return ActionRequest(
            "process-1",
            "run_process_v1",
            {"program": sys.executable, "args": list(args), "cwd": cwd},
        )

    def allow(self, *args: str, network: bool = False):
        return self.store.allow(
            sys.executable,
            args,
            workspace_root=self.root,
            workspace_fingerprint=self.fingerprint,
            network=network,
        )

    def test_exact_rule_matches_and_pins_the_resolved_program(self) -> None:
        rule = self.allow("-m", "unittest")

        match = self.store.match(
            self.request("-m", "unittest"),
            workspace_root=self.root,
            workspace_fingerprint=self.fingerprint,
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.rule_id, rule.id)
        self.assertEqual(Path(match.program_path), Path(sys.executable).resolve())
        self.assertIsNone(
            self.store.match(
                self.request("-m", "unittest", "extra"),
                workspace_root=self.root,
                workspace_fingerprint=self.fingerprint,
            )
        )

    def test_rule_is_bound_to_workspace_identity_and_descendant_cwd(self) -> None:
        self.allow("-V")
        (self.root / "sub").mkdir()

        self.assertIsNotNone(
            self.store.match(
                self.request("-V", cwd="sub"),
                workspace_root=self.root,
                workspace_fingerprint=self.fingerprint,
            )
        )
        self.assertIsNone(
            self.store.match(
                self.request("-V", cwd=".."),
                workspace_root=self.root,
                workspace_fingerprint=self.fingerprint,
            )
        )
        self.assertIsNone(
            self.store.match(
                self.request("-V"),
                workspace_root=self.root,
                workspace_fingerprint="copied-workspace",
            )
        )

    def test_network_command_requires_network_enabled_rule(self) -> None:
        request = self.request("-m", "pip", "install", "example")
        with self.assertRaisesRegex(ValueError, "requires --network"):
            self.allow("-m", "pip", "install", "example", network=False)
        rule = self.allow("-m", "pip", "install", "example", network=True)
        match = self.store.match(
            request,
            workspace_root=self.root,
            workspace_fingerprint=self.fingerprint,
        )
        self.assertEqual(match.rule_id if match else None, rule.id)

    def test_source_rule_applies_inside_a_managed_worktree(self) -> None:
        rule = self.allow("-V")
        worktree = self.base / "managed-worktree"
        worktree.mkdir()

        match = self.store.match(
            self.request("-V"),
            workspace_root=self.root,
            workspace_fingerprint=self.fingerprint,
            execution_root=worktree,
        )

        self.assertEqual(match.rule_id if match else None, rule.id)
        self.assertIsNone(
            self.store.match(
                self.request("-V", cwd=str(self.root)),
                workspace_root=self.root,
                workspace_fingerprint=self.fingerprint,
                execution_root=worktree,
            )
        )

    def test_list_and_unique_prefix_revoke_are_workspace_scoped(self) -> None:
        rule = self.allow("-V")

        self.assertEqual(
            self.store.list(
                workspace_root=self.root,
                workspace_fingerprint=self.fingerprint,
            ),
            (rule,),
        )
        revoked = self.store.revoke(
            rule.id[:8],
            workspace_root=self.root,
            workspace_fingerprint=self.fingerprint,
        )

        self.assertEqual(revoked.id, rule.id)
        self.assertEqual(
            self.store.list(
                workspace_root=self.root,
                workspace_fingerprint=self.fingerprint,
            ),
            (),
        )


if __name__ == "__main__":
    unittest.main()
