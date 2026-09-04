from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Sequence

from .models import (
    VerificationCommand,
    VerificationKind,
    VerificationRequest,
    VerificationUnavailable,
)

try:
    import tomli
except ImportError:
    tomli = None  # type: ignore[assignment]


class RiskTier(str, Enum):
    LOW = "low"          # Documentation, README, comments, copy, non-code assets -> No tests
    MEDIUM = "medium"    # Local feature code, pure functions, parsers, leaf modules -> Syntax + Targeted tests
    HIGH = "high"        # Core state machines, runtime, workspace, policy, sessions -> Targeted + Integration tests
    CRITICAL = "critical"# Dependencies, DB schema/migrations, build config, exports -> Full regression + Build gate


class VerificationPhase(str, Enum):
    IN_FLIGHT = "in_flight"          # During editing: L0 fast syntax check (< 5ms), fail-fast
    LOCAL_MILESTONE = "local_milestone" # Intermediate task checkpoint: syntax + targeted tests
    FINAL_GATE = "final_gate"        # Final completion candidate: affected tests + integration / full gate


@dataclass(frozen=True)
class SyntaxCheckResult:
    file_path: str
    is_valid: bool
    error_message: str | None = None
    line: int | None = None
    column: int | None = None

    def format_diagnostic(self) -> str:
        if self.is_valid:
            return f"{self.file_path}: syntax OK"
        location = ""
        if self.line is not None:
            location = f":{self.line}"
            if self.column is not None:
                location += f":{self.column}"
        return f"{self.file_path}{location}: {self.error_message or 'Syntax error'}"


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
        }


def check_syntax(
    file_path: str,
    source_text: str | None = None,
    *,
    workspace_root: Path | None = None,
) -> SyntaxCheckResult:
    """Perform zero-overhead fast syntax checking on a file (< 5ms)."""
    normalized = file_path.replace("\\", "/")
    if source_text is None:
        if workspace_root is not None:
            target = workspace_root / normalized
            if target.is_file():
                try:
                    source_text = target.read_text(encoding="utf-8", errors="replace")
                except OSError as err:
                    return SyntaxCheckResult(file_path, False, f"IO error reading file: {err}")
            else:
                return SyntaxCheckResult(file_path, False, "File does not exist")
        else:
            return SyntaxCheckResult(file_path, True, "No content provided for syntax check")

    if normalized.endswith(".py"):
        try:
            ast.parse(source_text, filename=normalized)
            compile(source_text, normalized, "exec")
            return SyntaxCheckResult(file_path, True)
        except (SyntaxError, IndentationError, TabError) as exc:
            return SyntaxCheckResult(
                file_path,
                False,
                error_message=str(exc.msg) if hasattr(exc, "msg") else str(exc),
                line=exc.lineno,
                column=exc.offset,
            )
        except Exception as exc:
            return SyntaxCheckResult(file_path, False, error_message=str(exc))

    if normalized.endswith(".json"):
        try:
            json.loads(source_text)
            return SyntaxCheckResult(file_path, True)
        except Exception as exc:
            line, col = None, None
            if isinstance(exc, json.JSONDecodeError):
                line, col = exc.lineno, exc.colno
            return SyntaxCheckResult(file_path, False, error_message=str(exc), line=line, column=col)

    if normalized.endswith(".toml") and tomli is not None:
        try:
            tomli.loads(source_text)
            return SyntaxCheckResult(file_path, True)
        except Exception as exc:
            return SyntaxCheckResult(file_path, False, error_message=str(exc))

    return SyntaxCheckResult(file_path, True)


_LOW_RISK_EXTENSIONS = frozenset(
    {
        ".md", ".markdown", ".txt", ".rst", ".adoc",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
        ".css", ".html", ".scss",
    }
)
_LOW_RISK_FILENAMES = frozenset(
    {
        "license", "license.md", "license.txt",
        ".gitignore", ".gitattributes", ".editorconfig",
        "readme", "readme.md",
    }
)

_CRITICAL_PATTERNS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-",
    "alembic",
    "migrations",
    "_database.py",
    "_schema_",
)

_HIGH_RISK_SUBSYSTEMS = (
    "core/",
    "runtime/",
    "policy/",
    "workspace/",
    "sessions/",
    "checkpoints/",
)


def classify_risk(changed_files: Sequence[str]) -> tuple[RiskTier, str]:
    """Classify the risk tier of a collection of changed files."""
    if not changed_files:
        return RiskTier.LOW, "No changed files detected"

    norm_files = [f.replace("\\", "/") for f in changed_files]

    # Check for critical changes
    for f in norm_files:
        lowered = f.casefold()
        for pattern in _CRITICAL_PATTERNS:
            if pattern in lowered:
                return RiskTier.CRITICAL, f"Critical configuration or schema modified: {f}"

    # Check for high risk subsystem changes
    for f in norm_files:
        lowered = f.casefold()
        for subsystem in _HIGH_RISK_SUBSYSTEMS:
            if subsystem in lowered:
                return RiskTier.HIGH, f"Core subsystem modified: {f}"

    # Check if ALL files are low risk
    all_low = True
    for f in norm_files:
        path = PurePosixPath(f)
        if path.suffix.casefold() in _LOW_RISK_EXTENSIONS:
            continue
        if path.name.casefold() in _LOW_RISK_FILENAMES:
            continue
        all_low = False
        break

    if all_low:
        return RiskTier.LOW, "All changed files are documentation or non-executable assets"

    return RiskTier.MEDIUM, "Changes affect local feature logic or modules"


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

    @property
    def semantic_graph(self) -> object | None:
        return self._semantic_graph

    def set_semantic_graph(self, graph: object) -> None:
        self._semantic_graph = graph

    def plan(
        self,
        changed_files: Sequence[str],
        phase: VerificationPhase = VerificationPhase.LOCAL_MILESTONE,
    ) -> VerificationPlan:
        if self._semantic_graph is not None and hasattr(self._semantic_graph, "evaluate_risk"):
            tier_val, reason = self._semantic_graph.evaluate_risk(changed_files)
            tier = RiskTier(tier_val)
        else:
            tier, reason = classify_risk(changed_files)

        syntax_targets = tuple(
            f for f in changed_files
            if f.endswith((".py", ".json", ".toml"))
        )

        if tier is RiskTier.LOW:
            return VerificationPlan(
                tier=tier,
                phase=phase,
                changed_files=tuple(changed_files),
                syntax_targets=syntax_targets,
                targeted_tests=(),
                require_full_gate=False,
                skip_tests=True,
                reason=reason,
            )

        if phase is VerificationPhase.IN_FLIGHT:
            return VerificationPlan(
                tier=tier,
                phase=phase,
                changed_files=tuple(changed_files),
                syntax_targets=syntax_targets,
                targeted_tests=(),
                require_full_gate=False,
                skip_tests=True,
                reason="In-flight phase performs fast syntax checking only (< 5ms)",
            )

        impacted = self.find_impacted_tests(changed_files)

        if phase is VerificationPhase.LOCAL_MILESTONE:
            return VerificationPlan(
                tier=tier,
                phase=phase,
                changed_files=tuple(changed_files),
                syntax_targets=syntax_targets,
                targeted_tests=impacted,
                require_full_gate=False,
                skip_tests=len(impacted) == 0,
                reason=reason,
            )

        # FINAL_GATE
        require_full = tier in {RiskTier.CRITICAL, RiskTier.HIGH}
        return VerificationPlan(
            tier=tier,
            phase=phase,
            changed_files=tuple(changed_files),
            syntax_targets=syntax_targets,
            targeted_tests=impacted,
            require_full_gate=require_full,
            skip_tests=False if require_full else (len(impacted) == 0),
            reason=reason,
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


@dataclass(frozen=True)
class PlanExecutionOutcome:
    plan: VerificationPlan
    syntax_passed: bool
    syntax_errors: tuple[SyntaxCheckResult, ...] = ()
    tests_skipped: bool = False
    request: VerificationRequest | None = None
    diagnostic: str = ""


class PlannerVerificationAdapter:
    """Bridge a VerificationPlan into concrete VerificationRequests or fast check outcomes."""

    def __init__(self, planner: VerificationPlanner) -> None:
        self.planner = planner

    def execute_syntax_checks(
        self, plan: VerificationPlan, file_contents: dict[str, str] | None = None
    ) -> tuple[bool, tuple[SyntaxCheckResult, ...]]:
        """Execute fast L0 syntax checks on all syntax targets."""
        errors: list[SyntaxCheckResult] = []
        for target in plan.syntax_targets:
            text = file_contents.get(target) if file_contents else None
            result = check_syntax(target, text, workspace_root=self.planner._root)
            if not result.is_valid:
                errors.append(result)
        return len(errors) == 0, tuple(errors)

    def prepare_request(
        self,
        plan: VerificationPlan,
        default_kind: VerificationKind = VerificationKind.PYTHON_UNITTEST,
    ) -> PlanExecutionOutcome:
        """Prepare execution outcome or concrete VerificationRequest."""
        syntax_ok, errors = self.execute_syntax_checks(plan)
        if not syntax_ok:
            diag = "; ".join(e.format_diagnostic() for e in errors)
            return PlanExecutionOutcome(
                plan=plan,
                syntax_passed=False,
                syntax_errors=errors,
                tests_skipped=True,
                request=None,
                diagnostic=f"Syntax check failed: {diag}",
            )

        if plan.skip_tests:
            return PlanExecutionOutcome(
                plan=plan,
                syntax_passed=True,
                syntax_errors=(),
                tests_skipped=True,
                request=None,
                diagnostic=f"Tests bypassed safely ({plan.tier.value} risk): {plan.reason}",
            )

        # Determine targets for VerificationRequest
        if plan.require_full_gate or not plan.targeted_tests:
            targets: tuple[str, ...] = ()
        else:
            targets = (plan.targeted_tests[0],) if default_kind is VerificationKind.PYTHON_UNITTEST else plan.targeted_tests

        request = VerificationRequest(
            kind=default_kind,
            targets=targets,
        )
        return PlanExecutionOutcome(
            plan=plan,
            syntax_passed=True,
            syntax_errors=(),
            tests_skipped=False,
            request=request,
            diagnostic=f"Executing targeted verification for {plan.tier.value} risk tier",
        )
