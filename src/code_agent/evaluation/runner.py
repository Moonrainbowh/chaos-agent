from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from .models import Scenario, ScenarioPrompt, ScenarioResult


ScenarioExecutor = Callable[[Path, ScenarioPrompt], Awaitable[ScenarioResult]]


class ScenarioRunner:
    """Execute each scenario in a disposable copy of its fixture workspace."""

    async def run(self, scenario: Scenario, execute: ScenarioExecutor) -> tuple[ScenarioResult, Path]:
        if not isinstance(scenario, Scenario) or not callable(execute):
            raise TypeError("scenario and execute must be valid")
        temporary = Path(tempfile.mkdtemp(prefix="chaos-evaluation-"))
        workspace = temporary / "workspace"
        try:
            await asyncio.to_thread(shutil.copytree, scenario.fixture_root, workspace)
            await asyncio.to_thread(_materialize_fixture_files, workspace, scenario.fixture_files)
            result = await execute(workspace, scenario.prompt())
            if not isinstance(result, ScenarioResult):
                raise TypeError("scenario executor must return ScenarioResult")
            return result, workspace
        except Exception:
            await asyncio.to_thread(shutil.rmtree, temporary, True)
            raise


def _materialize_fixture_files(workspace: Path, files: object) -> None:
    if not isinstance(files, dict):
        raise TypeError("fixture files must be a mapping")
    for relative_path, content in files.items():
        path = workspace / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
