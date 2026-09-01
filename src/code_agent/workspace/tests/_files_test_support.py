from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402


class CountingScandir:
    def __init__(self, iterator: object, consumed: list[str]) -> None:
        self.iterator = iterator
        self.consumed = consumed

    def __enter__(self) -> "CountingScandir":
        self.iterator.__enter__()  # type: ignore[attr-defined]
        return self

    def __exit__(self, *args: object) -> None:
        self.iterator.__exit__(*args)  # type: ignore[attr-defined]

    def __iter__(self) -> "CountingScandir":
        return self

    def __next__(self) -> os.DirEntry[str]:
        entry = next(self.iterator)  # type: ignore[arg-type]
        self.consumed.append(entry.name)
        return entry


class WorkspaceFilesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.guard = WorkspacePathGuard(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def files(self) -> WorkspaceFiles:
        return WorkspaceFiles(self.guard, IgnoreRules.from_workspace(self.root))
