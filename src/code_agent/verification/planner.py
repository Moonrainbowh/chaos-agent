from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Sequence

from .risk import RiskTier, classify_risk
from .syntax_check import SyntaxCheckResult, check_syntax


class VerificationPhase(str, Enum):
    IN_FLIGHT = "in_flight"          # During editing: L0 fast syntax check (< 5ms), fail-fast
    LOCAL_MILESTONE = "local_milestone" # Intermediate task checkpoint: syntax + targeted tests
    FINAL_GATE = "final_gate"        # Final completion candidate: affected tests + integration / full gate


@dataclass(frozen=True)
class VerificationPlan:
    tier: RiskTier
    phase: VerificationPhase
    changed_files: tuple[str, ...]
    syntax_targets: tuple[str, ...]
    targeted_tests: tuple[str, ...]
    require_full_gate: bool
    skip_tests: bool
    reason: str
    semantic_generation: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier.value,
            "phase": self.phase.value,
            "changed_files": list(self.changed_files),
            "syntax_targets": list(self.syntax_targets),
            "targeted_tests": list(self.targeted_tests),
            "require_full_gate": self.require_full_gate,
            "skip_tests": self.skip_tests,
            "reason": self.reason,
            "semantic_generation": self.semantic_generation,
        }


class VerificationPlanner:
    """Determine what, when, and how much to verify based on a ChangeSet.

    Can be powered by UnifiedSemanticGraph as the shared code cognition foundation.
    """

    def __init__(
        self,
        workspace_root: Path,
        semantic_graph: object | None = None,
    ) -> None:
        if not isinstance(workspace_root, Path):
            raise TypeError("workspace_root must be a Path")
        self._root = workspace_root.resolve(strict=False)
        self._semantic_graph = semantic_graph
        self._semantic_generation: int | None = None

    @property
    def semantic_graph(self) -> object | None:
        return self._semantic_graph

    def set_semantic_graph(self, graph: object) -> None:
        self._semantic_graph = graph
        self._semantic_generation = None

    def set_semantic_snapshot(self, snapshot: object) -> None:
        graph = getattr(snapshot, "semantic_graph", None)
        generation = getattr(snapshot, "generation", None)
        if graph is None or isinstance(generation, bool) or not isinstance(
            generation, int
        ):
            raise TypeError("semantic snapshot must provide graph and generation")
        self._semantic_graph = graph
        self._semantic_generation = generation

    def plan(
        self,
        changed_files: Sequence[str],
        phase: VerificationPhase = VerificationPhase.LOCAL_MILESTONE,
    ) -> VerificationPlan:
        if self._semantic_graph is not None and hasattr(
            self._semantic_graph, "evaluate_risk"
        ):
            tier_val, reason = self._semantic_graph.evaluate_risk(changed_files)
            tier = RiskTier(tier_val)
        else:
            tier, reason = classify_risk(changed_files)
        changed = tuple(changed_files)
        syntax_targets = tuple(
            f for f in changed_files
            if f.endswith((".py", ".json", ".toml"))
        )
        if tier is RiskTier.LOW:
            return self._make_plan(
                tier, phase, changed, syntax_targets, (), False, True, reason
            )
        if phase is VerificationPhase.IN_FLIGHT:
            return self._make_plan(
                tier, phase, changed, syntax_targets, (), False, True,
                "In-flight phase performs fast syntax checking only (< 5ms)",
            )
        impacted = self.find_impacted_tests(changed_files)
        if phase is VerificationPhase.LOCAL_MILESTONE:
            return self._make_plan(
                tier, phase, changed, syntax_targets, impacted, False,
                not impacted, reason,
            )
        require_full = tier in {RiskTier.CRITICAL, RiskTier.HIGH}
        return self._make_plan(
            tier, phase, changed, syntax_targets, impacted, require_full,
            False if require_full else not impacted, reason,
        )

    def _make_plan(
        self,
        tier: RiskTier,
        phase: VerificationPhase,
        changed: tuple[str, ...],
        syntax_targets: tuple[str, ...],
        targeted_tests: tuple[str, ...],
        require_full_gate: bool,
        skip_tests: bool,
        reason: str,
    ) -> VerificationPlan:
        return VerificationPlan(
            tier, phase, changed, syntax_targets, targeted_tests,
            require_full_gate, skip_tests, reason, self._semantic_generation,
        )

    def find_impacted_tests(self, changed_files: Sequence[str]) -> tuple[str, ...]:
        """Identify the test targets affected by the changed files."""
        discovered: set[str] = set()

        # 1. Semantic Graph reverse dependency traversal if available
        if self._semantic_graph is not None and hasattr(self._semantic_graph, "find_impacted_tests"):
            graph_impacted = self._semantic_graph.find_impacted_tests(changed_files)
            discovered.update(graph_impacted)

        # 2. Convention and directory heuristics
        for file_str in changed_files:
            normalized = file_str.replace("\\", "/")
            path = PurePosixPath(normalized)
            name = path.name

            # If the file is already a test file, add it directly
            if name.startswith("test_") or name.endswith("_test.py"):
                discovered.add(normalized)
                continue

            if not normalized.endswith(".py"):
                continue

            stem = path.stem
            parent = path.parent

            # Sibling tests/ directory
            candidate_tests_dir = self._root / parent.as_posix() / "tests"
            if candidate_tests_dir.is_dir():
                specific = candidate_tests_dir / f"test_{stem}.py"
                if specific.is_file():
                    rel = specific.relative_to(self._root).as_posix()
                    discovered.add(rel)
                else:
                    rel_dir = candidate_tests_dir.relative_to(self._root).as_posix()
                    discovered.add(rel_dir)

            # Root tests/ directory matching
            root_tests_dir = self._root / "tests"
            if root_tests_dir.is_dir():
                specific_root = root_tests_dir / f"test_{stem}.py"
                if specific_root.is_file():
                    rel = specific_root.relative_to(self._root).as_posix()
                    discovered.add(rel)

        return tuple(sorted(discovered))

    def get_review_scope(self, changed_files: Sequence[str]) -> tuple[str, ...]:
        """Determine the review blast-radius for a set of changed files."""
        if self._semantic_graph is not None and hasattr(self._semantic_graph, "get_review_scope"):
            return self._semantic_graph.get_review_scope(changed_files)
        return tuple(sorted(set(changed_files).union(self.find_impacted_tests(changed_files))))

    def plan_refactor_order(self, files: Sequence[str]) -> tuple[str, ...]:
        """Determine the safe step-by-step refactoring order for multiple files."""
        if self._semantic_graph is not None and hasattr(self._semantic_graph, "plan_refactor_order"):
            return self._semantic_graph.plan_refactor_order(files)
        return tuple(sorted(files))


from .planner_adapter import PlanExecutionOutcome, PlannerVerificationAdapter

__all__ = [
    "PlanExecutionOutcome",
    "PlannerVerificationAdapter",
    "RiskTier",
    "SyntaxCheckResult",
    "VerificationPhase",
    "VerificationPlan",
    "VerificationPlanner",
    "check_syntax",
    "classify_risk",
]
