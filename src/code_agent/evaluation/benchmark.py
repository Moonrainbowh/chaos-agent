from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .fingerprint import corpus_fingerprint
from .grader import DeterministicGrader, Grade
from .models import Scenario, ScenarioResult
from .observation import HarnessObservation
from .report import EvaluationMetrics, render_markdown_report
from .runner import ScenarioExecutor, ScenarioRunner


@dataclass(frozen=True)
class BenchmarkRecord:
    """Canonical source for grades, metrics, gates, and reports."""

    scenario: Scenario
    result: ScenarioResult
    observation: HarnessObservation
    grade: Grade


@dataclass(frozen=True)
class ReplayBenchmarkResult:
    records: tuple[BenchmarkRecord, ...]
    corpus_fingerprint: str

    def __post_init__(self) -> None:
        records = tuple(self.records)
        if not records or not all(isinstance(item, BenchmarkRecord) for item in records):
            raise ValueError("benchmark records must be non-empty and typed")
        object.__setattr__(self, "records", records)
        if not isinstance(self.corpus_fingerprint, str) or len(self.corpus_fingerprint) != 64:
            raise ValueError("corpus fingerprint must be a SHA-256 digest")

    @property
    def grades(self) -> tuple[tuple[str, Grade], ...]:
        return tuple((record.scenario.identifier, record.grade) for record in self.records)

    @property
    def metrics(self) -> EvaluationMetrics:
        return EvaluationMetrics.from_records(self.records)

    def to_json(self) -> str:
        return json.dumps(
            {
                "corpus_fingerprint": self.corpus_fingerprint,
                "metrics": json.loads(self.metrics.to_json()),
                "scenarios": [_record_json(record) for record in self.records],
            },
            sort_keys=True,
        )

    def to_markdown(self) -> str:
        failed = [identifier for identifier, grade in self.grades if not grade.passed]
        infrastructure = sum(bool(record.observation.infrastructure_failures) for record in self.records)
        lines = [
            render_markdown_report(self.metrics),
            "",
            f"- Corpus fingerprint: `{self.corpus_fingerprint}`",
            f"- Infrastructure failures: {infrastructure}",
            "- Failures: none" if not failed else "- Failures: " + ", ".join(failed),
        ]
        return "\n".join(lines)

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
    """Run a fixed scenario corpus and grade only trusted harness evidence."""

    def __init__(
        self,
        runner: ScenarioRunner | None = None,
        grader: DeterministicGrader | None = None,
    ) -> None:
        self._runner = runner or ScenarioRunner()
        self._grader = grader or DeterministicGrader()

    async def run(
        self,
        scenarios: Sequence[Scenario],
        execute: ScenarioExecutor,
    ) -> ReplayBenchmarkResult:
        checked = tuple(scenarios)
        if not checked or not all(isinstance(item, Scenario) for item in checked) or not callable(execute):
            raise ValueError("scenarios and executor must be valid")
        records: list[BenchmarkRecord] = []
        for scenario in checked:
            result, observation = await self._runner.run(scenario, execute)
            grade = self._grader.grade(scenario, result, observation)
            records.append(BenchmarkRecord(scenario, result, observation, grade))
        return ReplayBenchmarkResult(tuple(records), corpus_fingerprint(checked))


def _record_json(record: BenchmarkRecord) -> dict[str, object]:
    observation = record.observation
    return {
        "id": record.scenario.identifier,
        "category": record.scenario.category,
        "passed": record.grade.passed,
        "failures": list(record.grade.failures),
        "verifiers": [_verifier_json(item) for item in observation.verifier_results],
        "final_digest": observation.final_digest,
        "changed_paths": list(observation.changed_paths),
        "timed_out": observation.timed_out,
        "workspace_removed": observation.workspace_removed,
        "outside_workspace_paths": list(observation.outside_workspace_paths),
        "isolation_mode": observation.isolation_mode,
        "termination_confirmed": observation.termination_confirmed,
        "infrastructure_failures": list(observation.infrastructure_failures),
    }


def _verifier_json(item: object) -> dict[str, object]:
    return {
        "name": getattr(item, "name"),
        "baseline_exit_code": getattr(item, "baseline_exit_code"),
        "final_exit_code": getattr(item, "final_exit_code"),
        "baseline_timed_out": getattr(item, "baseline_timed_out"),
        "final_timed_out": getattr(item, "final_timed_out"),
        "workspace_digest": getattr(item, "workspace_digest"),
        "infrastructure_failures": list(getattr(item, "infrastructure_failures")),
    }
