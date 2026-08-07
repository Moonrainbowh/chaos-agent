from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict

from .models import Scenario


def corpus_fingerprint(scenarios: Sequence[Scenario]) -> str:
    """Hash every behaviorally relevant field while excluding host fixture paths."""
    payload = [_scenario_payload(scenario) for scenario in scenarios]
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _scenario_payload(scenario: Scenario) -> dict[str, object]:
    return {
        "identifier": scenario.identifier,
        "category": scenario.category,
        "fixture_version": scenario.fixture_version,
        "user_request": scenario.user_request,
        "allowed_workspace": scenario.allowed_workspace,
        "forbidden_actions": scenario.forbidden_actions,
        "required_verifiers": scenario.required_verifiers,
        "forbidden_verifiers": scenario.forbidden_verifiers,
        "budgets": (
            scenario.max_model_turns,
            scenario.max_tool_calls,
            scenario.max_active_seconds,
        ),
        "fixture_files": dict(sorted(scenario.fixture_files.items())),
        "golden_files": dict(sorted(scenario.golden_files.items())),
        "golden_absent_files": scenario.golden_absent_files,
        "expected": _expected_payload(scenario),
    }


def _expected_payload(scenario: Scenario) -> dict[str, object]:
    expected = scenario.expected
    return {
        "task_status": expected.task_status,
        "forbidden_policy_events": expected.forbidden_policy_events,
        "workspace": asdict(expected.workspace),
        "verifiers": [asdict(verifier) for verifier in expected.verifiers],
        "lifecycle": asdict(expected.lifecycle),
    }
