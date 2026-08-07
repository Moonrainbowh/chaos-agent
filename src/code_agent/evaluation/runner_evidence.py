from __future__ import annotations

from collections.abc import Mapping

from .models import VerifierOracle
from .observation import TrustedVerifierResult
from .verifier import CommandOutcome


def combine_verifiers(
    oracles: tuple[VerifierOracle, ...],
    baseline: Mapping[str, CommandOutcome],
    final: Mapping[str, CommandOutcome],
    final_digest: str,
) -> tuple[TrustedVerifierResult, ...]:
    combined: list[TrustedVerifierResult] = []
    for oracle in oracles:
        before = baseline[oracle.name]
        after = final.get(oracle.name, CommandOutcome(None))
        combined.append(
            TrustedVerifierResult(
                oracle.name,
                before.exit_code,
                after.exit_code,
                before.timed_out,
                after.timed_out,
                final_digest,
                before.infrastructure_failure,
                after.infrastructure_failure,
            )
        )
    return tuple(combined)


def infrastructure_failures(
    execution_failure: str | None,
    verifiers: tuple[TrustedVerifierResult, ...],
    final_links: tuple[str, ...],
) -> tuple[str, ...]:
    failures = [execution_failure] if execution_failure else []
    failures.extend(f"workspace link is not allowed: {path}" for path in final_links)
    for result in verifiers:
        failures.extend(result.infrastructure_failures)
    return tuple(dict.fromkeys(failures))
