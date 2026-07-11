from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import (  # noqa: E402
    PathOutsideWorkspace,
    SensitivePathError,
)
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class WorkspacePathGuardTests(unittest.TestCase):
    def test_external_access_requires_explicit_guard_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "workspace"
            outside = base / "outside.txt"
            root.mkdir()
            outside.write_text("outside\n", encoding="utf-8")

            strict = WorkspacePathGuard(root)
            with self.assertRaises(PathOutsideWorkspace):
                strict.resolve(outside)

            permissive = WorkspacePathGuard(root, allow_outside=True)
            self.assertEqual(permissive.resolve(outside), outside)

    def test_external_sensitive_path_needs_its_own_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "workspace"
            private_key = base / "id_ed25519"
            root.mkdir()
            private_key.write_text("private", encoding="utf-8")

            guard = WorkspacePathGuard(root, allow_outside=True)
            with self.assertRaises(SensitivePathError):
                guard.resolve(private_key)
            self.assertEqual(
                WorkspacePathGuard(
                    root, allow_outside=True, allow_sensitive=True
                ).resolve(private_key),
                private_key,
            )
    def test_root_must_be_an_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            missing = base / "missing"
            regular_file = base / "file.txt"
            regular_file.write_text("x", encoding="utf-8")

            for invalid in (missing, regular_file):
                with self.subTest(root=invalid):
                    with self.assertRaises(ValueError):
                        WorkspacePathGuard(invalid)

    def test_resolves_relative_and_real_absolute_paths_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / "src" / "main.py"
            target.parent.mkdir()
            target.write_text("pass\n", encoding="utf-8")
            guard = WorkspacePathGuard(root)

            self.assertEqual(guard.resolve("src/main.py"), target)
            self.assertEqual(guard.resolve(target), target)
            self.assertEqual(guard.relative(target), Path("src/main.py"))

    def test_rejects_absolute_parent_and_nul_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "workspace"
            root.mkdir()
            guard = WorkspacePathGuard(root)

            for unsafe in (base / "outside.txt", "../outside.txt", "bad\0name"):
                with self.subTest(path=unsafe):
                    with self.assertRaises(PathOutsideWorkspace):
                        guard.resolve(unsafe, for_write=True)

    def test_rejects_existing_link_that_escapes_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "workspace"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            link = root / "linked"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"directory symlinks unavailable: {error}")

            with self.assertRaises(PathOutsideWorkspace):
                WorkspacePathGuard(root).resolve("linked/secret.txt", for_write=True)

    def test_protects_internal_metadata_and_sensitive_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            guard = WorkspacePathGuard(root)

            protected = (".git/config", ".code-agent/state.json")
            sensitive = (".env", ".env.production", "id_rsa", "server.pem")
            for path in protected + sensitive:
                with self.subTest(path=path):
                    with self.assertRaises(SensitivePathError):
                        guard.resolve(path, for_write=True)

            for allowed in (".env.example", ".env.sample", ".env.template"):
                with self.subTest(path=allowed):
                    self.assertEqual(guard.resolve(allowed), root / allowed)

            permissive = WorkspacePathGuard(root, allow_sensitive=True)
            self.assertEqual(permissive.resolve("server.pem"), root / "server.pem")
            with self.assertRaises(SensitivePathError):
                permissive.resolve(".git/config")

    def test_protects_local_api_config_directory_even_when_it_is_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_home = Path(temporary).resolve() / "code-agent"
            config_home.mkdir()
            (config_home / "config.toml").write_text("api_key = 'x'", encoding="utf-8")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(config_home.parent)}, clear=False):
                guard = WorkspacePathGuard(config_home)
                with self.assertRaises(SensitivePathError):
                    guard.resolve("config.toml")

    def test_parent_components_cannot_hide_a_protected_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "src").mkdir()
            guard = WorkspacePathGuard(root)

            with self.assertRaises(SensitivePathError):
                guard.resolve("src/../.git/config")

    def test_protects_metadata_directories_at_any_depth_case_insensitively(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            guard = WorkspacePathGuard(root)

            for path in ("nested/.GiT/config", "deep/pkg/.CODE-Agent/state.json"):
                with self.subTest(path=path):
                    with self.assertRaises(SensitivePathError):
                        guard.resolve(path, for_write=True)

    def test_rejects_mocked_link_like_component_without_os_link_privilege(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            linked = root / "linked"
            linked.mkdir()
            guard = WorkspacePathGuard(root)

            with patch(
                "code_agent.workspace.paths._is_link_like",
                side_effect=lambda path: path == linked,
            ):
                with self.assertRaises(PathOutsideWorkspace):
                    guard.resolve("linked/inside.txt", for_write=True)


class IgnoreRulesTests(unittest.TestCase):
    def test_builtin_directories_are_always_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rules = IgnoreRules.from_workspace(Path(temporary))

            for path in (".git/config", ".code-agent/log", "a/__pycache__/x.pyc"):
                with self.subTest(path=path):
                    self.assertTrue(rules.is_ignored(path))

    def test_common_gitignore_rules_and_ordered_negation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_text(
                "# generated\n"
                "\n"
                "build/\n"
                "*.log\n"
                "!keep.log\n"
                "/root-only.txt\n"
                "cache/*.tmp\n"
                "!cache/keep.tmp\n",
                encoding="utf-8",
            )
            rules = IgnoreRules.from_workspace(root)

            expected = {
                "build/output.bin": True,
                "nested/build/output.bin": True,
                "run.log": True,
                "nested/run.log": True,
                "keep.log": False,
                "root-only.txt": True,
                "nested/root-only.txt": False,
                "cache/drop.tmp": True,
                "cache/keep.tmp": False,
                "nested/cache/drop.tmp": False,
            }
            for path, ignored in expected.items():
                with self.subTest(path=path):
                    self.assertEqual(rules.is_ignored(path), ignored)

    def test_directory_status_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
            rules = IgnoreRules.from_workspace(root)

            self.assertTrue(rules.is_ignored("artifacts", is_dir=True))
            self.assertTrue(rules.is_ignored("artifacts/data.csv"))
            self.assertFalse(rules.is_ignored("artifacts.txt"))

    def test_escaped_leading_bang_and_hash_are_literal_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".gitignore").write_text(
                "\\!literal.txt\n\\#notes.txt\n", encoding="utf-8"
            )
            rules = IgnoreRules.from_workspace(root)

            self.assertTrue(rules.is_ignored("!literal.txt"))
            self.assertTrue(rules.is_ignored("#notes.txt"))
            self.assertFalse(rules.is_ignored("literal.txt"))


if __name__ == "__main__":
    unittest.main()
