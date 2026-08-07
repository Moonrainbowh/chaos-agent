from __future__ import annotations

from pathlib import Path

from .fixtures import FixtureDefinition, bugfix_fixture, multifile_fixture, recovery_fixture, safety_fixture
from .models import Scenario, ScenarioExpectedOutcome


EXPECTED_CATEGORY_COUNTS = {
    "bugfix": 12,
    "multifile": 10,
    "recovery": 10,
    "safety": 8,
}
EXPECTED_SCENARIO_IDS = frozenset(
    [
        *(f"python-{number:02d}" for number in range(1, 7)),
        *(f"node-{number:02d}" for number in range(1, 4)),
        *(f"dotnet-{number:02d}" for number in range(1, 4)),
        *(f"python-multifile-{number:02d}" for number in range(1, 5)),
        *(f"node-multifile-{number:02d}" for number in range(1, 4)),
        *(f"dotnet-multifile-{number:02d}" for number in range(1, 4)),
        *(f"windows_resume-{number:02d}" for number in range(1, 11)),
        *(f"security-{number:02d}" for number in range(1, 9)),
    ]
)

# Updated only when the reviewed fixed corpus intentionally changes.
EXPECTED_CORPUS_FINGERPRINT = "c754039a3738abde42ee4d11fb7122bd4132bff3b8dcbf55e629835a1f0df2d5"


def fixed_replay_catalog(fixture_root: Path) -> tuple[Scenario, ...]:
    """Build the deterministic 40-scenario corpus without materializing hidden oracle data."""
    if not isinstance(fixture_root, Path) or not fixture_root.is_dir():
        raise ValueError("fixture_root must be an existing directory")
    scenarios = [
        *_language_group(fixture_root, "bugfix", (("python", 6), ("node", 3), ("dotnet", 3))),
        *_language_group(fixture_root, "multifile", (("python", 4), ("node", 3), ("dotnet", 3))),
        *(_scenario(fixture_root, f"windows_resume-{number:02d}", "recovery", "python", recovery_fixture(number)) for number in range(1, 11)),
        *(_scenario(fixture_root, f"security-{number:02d}", "safety", "decision", safety_fixture(number)) for number in range(1, 9)),
    ]
    _validate_catalog(scenarios)
    return tuple(scenarios)


def _language_group(
    root: Path,
    category: str,
    counts: tuple[tuple[str, int], ...],
) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for language, count in counts:
        for number in range(1, count + 1):
            identifier = _language_identifier(category, language, number)
            definition = (
                bugfix_fixture(language, number)
                if category == "bugfix"
                else multifile_fixture(language, number)
            )
            scenarios.append(_scenario(root, identifier, category, language, definition))
    return scenarios


def _language_identifier(category: str, language: str, number: int) -> str:
    return f"{language}-{number:02d}" if category == "bugfix" else f"{language}-multifile-{number:02d}"


def _scenario(
    root: Path,
    identifier: str,
    category: str,
    language: str,
    definition: FixtureDefinition,
) -> Scenario:
    expected = ScenarioExpectedOutcome(
        definition.status,
        forbidden_policy_events=("unsafe_execution",),
        workspace=definition.workspace,
        verifiers=definition.verifiers,
        lifecycle=definition.lifecycle,
    )
    required_verifiers = tuple(verifier.name for verifier in definition.verifiers)
    return Scenario(
        identifier,
        root,
        definition.request,
        expected,
        fixture_version=f"catalog-v3/{category}/{language}",
        category=category,
        forbidden_actions=definition.forbidden_actions,
        required_verifiers=required_verifiers,
        forbidden_verifiers=("raw_shell",),
        max_model_turns=16,
        max_tool_calls=32,
        max_active_seconds=120,
        fixture_files={
            **definition.files,
            "目录 空格/说明.txt": f"scenario {identifier}\n",
        },
        golden_files=definition.golden_files,
        golden_absent_files=definition.golden_absent_files,
    )


def _validate_catalog(scenarios: list[Scenario]) -> None:
    identifiers = [scenario.identifier for scenario in scenarios]
    if len(scenarios) != 40 or len(set(identifiers)) != 40:
        raise RuntimeError("fixed replay catalog must contain exactly 40 unique scenarios")
    actual = {
        category: sum(scenario.category == category for scenario in scenarios)
        for category in EXPECTED_CATEGORY_COUNTS
    }
    if actual != EXPECTED_CATEGORY_COUNTS:
        raise RuntimeError(f"fixed replay catalog category counts changed: {actual}")
