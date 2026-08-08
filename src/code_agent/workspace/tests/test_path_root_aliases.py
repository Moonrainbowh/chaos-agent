from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.errors import PathOutsideWorkspace  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class WorkspaceRootAliasTests(unittest.TestCase):
    def test_host_root_spelling_is_mapped_to_canonical_root_before_use(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            supplied_root = base / "supplied"
            canonical_root = base / "canonical"
            supplied_root.mkdir()
            canonical_root.mkdir()
            canonical_target = canonical_root / "file.py"
            canonical_target.write_text("canonical\n", encoding="utf-8")
            (supplied_root / "file.py").write_text(
                "other\n", encoding="utf-8"
            )
            real_resolve = Path.resolve

            def simulate_system_alias(path: Path, strict: bool = False) -> Path:
                if path == supplied_root:
                    return canonical_root
                return real_resolve(path, strict=strict)

            with patch.object(Path, "resolve", simulate_system_alias):
                guard = WorkspacePathGuard(supplied_root)

            self.assertEqual(
                guard.resolve(supplied_root / "file.py"), canonical_target
            )
            self.assertEqual(
                guard.relative_literal(supplied_root / "file.py"),
                Path("file.py"),
            )

    def test_absolute_paths_accept_the_exact_supplied_root_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            canonical_parent = base / "canonical"
            canonical_parent.mkdir()
            alias = base / "alias"
            self._symlink_or_skip(alias, canonical_parent)
            supplied_root = alias / "workspace"
            supplied_root.mkdir()
            target = supplied_root / "src" / "main.py"
            target.parent.mkdir()
            target.write_text("pass\n", encoding="utf-8")

            guard = WorkspacePathGuard(supplied_root)

            self.assertEqual(
                guard.resolve(target),
                canonical_parent / "workspace" / "src" / "main.py",
            )
            self.assertEqual(guard.relative(target), Path("src/main.py"))
            self.assertEqual(
                guard.relative_literal(target), Path("src/main.py")
            )

    def test_different_symlink_alias_is_not_authorized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "workspace"
            root.mkdir()
            other_alias = base / "other-alias"
            self._symlink_or_skip(other_alias, root)

            with self.assertRaises(PathOutsideWorkspace):
                WorkspacePathGuard(root).resolve(other_alias / "file.py")

    def test_supplied_root_alias_is_not_followed_after_guard_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            original_parent = base / "original"
            replacement_parent = base / "replacement"
            original_root = original_parent / "workspace"
            replacement_root = replacement_parent / "workspace"
            original_root.mkdir(parents=True)
            replacement_root.mkdir(parents=True)
            original = original_root / "file.py"
            replacement = replacement_root / "file.py"
            original.write_text("original\n", encoding="utf-8")
            replacement.write_text("replacement\n", encoding="utf-8")
            alias = base / "alias"
            self._symlink_or_skip(alias, original_parent)
            guard = WorkspacePathGuard(alias / "workspace")
            alias.unlink()
            alias.symlink_to(replacement_parent, target_is_directory=True)

            supplied_path = alias / "workspace" / "file.py"
            self.assertEqual(guard.resolve(supplied_path), original)
            self.assertEqual(
                guard.relative_literal(supplied_path), Path("file.py")
            )

    def _symlink_or_skip(self, alias: Path, target: Path) -> None:
        try:
            alias.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"directory symlinks unavailable: {error}")


if __name__ == "__main__":
    unittest.main()
