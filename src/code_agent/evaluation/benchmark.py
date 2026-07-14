from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .grader import DeterministicGrader, Grade
from .models import Scenario, ScenarioResult
from .report import EvaluationMetrics, render_markdown_report
from .runner import ScenarioExecutor, ScenarioRunner


@dataclass(frozen=True)
class ReplayBenchmarkResult:
    metrics: EvaluationMetrics
    grades: tuple[tuple[str, Grade], ...]
    records: tuple[tuple[Scenario, ScenarioResult, Grade], ...] = ()

    def to_json(self) -> str:
        return json.dumps(
            {
                "metrics": json.loads(self.metrics.to_json()),
                "scenarios": [
                    {"id": identifier, "passed": grade.passed, "failures": list(grade.failures)}
                    for identifier, grade in self.grades
                ],
            },
            sort_keys=True,
        )

    def to_markdown(self) -> str:
        failed = [identifier for identifier, grade in self.grades if not grade.passed]
        suffix = "- Failures: none" if not failed else "- Failures: " + ", ".join(failed)
        return render_markdown_report(self.metrics) + "\n" + suffix

    def write_reports(self, output_directory: Path) -> tuple[Path, Path]:
        """Write the replay result as reviewable JSON and Markdown artifacts."""
        if not isinstance(output_directory, Path):
            raise TypeError("output_directory must be a Path")
        output_directory.mkdir(parents=True, exist_ok=True)
        if not output_directory.is_dir():
            raise ValueError("output_directory must be a directory")
        json_path = output_directory / "replay-benchmark.json"
        markdown_path = output_directory / "replay-benchmark.md"
        json_path.write_text(self.to_json() + "\n", encoding="utf-8")
        markdown_path.write_text(self.to_markdown() + "\n", encoding="utf-8")
        return json_path, markdown_path


class ReplayBenchmark:
    """Run a fixed scenario corpus and grade only workspace/result evidence."""

    def __init__(self, runner: ScenarioRunner | None = None, grader: DeterministicGrader | None = None) -> None:
        self._runner = runner or ScenarioRunner()
        self._grader = grader or DeterministicGrader()

    async def run(self, scenarios: Sequence[Scenario], execute: ScenarioExecutor) -> ReplayBenchmarkResult:
        checked = tuple(scenarios)
        if not checked or not all(isinstance(scenario, Scenario) for scenario in checked) or not callable(execute):
            raise ValueError("scenarios and executor must be valid")
        rows: list[tuple[ScenarioResult, Grade]] = []
        grades: list[tuple[str, Grade]] = []
        records: list[tuple[Scenario, ScenarioResult, Grade]] = []
        for scenario in checked:
            result, workspace = await self._runner.run(scenario, execute)
            grade = self._grader.grade(scenario, result, workspace)
            rows.append((result, grade))
            grades.append((scenario.identifier, grade))
            records.append((scenario, result, grade))
        return ReplayBenchmarkResult(EvaluationMetrics.from_results(rows), tuple(grades), tuple(records))
