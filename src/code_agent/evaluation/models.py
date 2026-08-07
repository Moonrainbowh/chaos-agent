from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .paths import checked_relative


def _text(value: object, name: str, limit: int = 4_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be bounded non-blank text")
    return value


def _texts(values: object, name: str, limit: int = 64) -> tuple[str, ...]:
    checked = tuple(values) if not isinstance(values, str) else ()
    if len(checked) > limit or not all(isinstance(value, str) and value for value in checked):
        raise ValueError(f"{name} must contain bounded text")
    return checked


def _relative(value: str, name: str) -> str:
    return checked_relative(value, name)


def _file_map(values: Mapping[str, str], name: str) -> dict[str, str]:
    checked = dict(values)
    if len(checked) > 96 or not all(isinstance(content, str) for content in checked.values()):
        raise ValueError(f"{name} must be a bounded text mapping")
    return {_relative(path, name): content for path, content in checked.items()}


@dataclass(frozen=True)
class WorkspaceOracle:
    """Hidden constraints over the complete before/after workspace manifest."""

    required_changes: tuple[str, ...] = ()
    allowed_changes: tuple[str, ...] = ("*",)
    protected_files: tuple[str, ...] = ()
    exact_files: Mapping[str, str] = field(default_factory=dict)
    absent_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("required_changes", "allowed_changes", "protected_files", "absent_files"):
            values = tuple(_relative(value, name) for value in _texts(getattr(self, name), name))
            object.__setattr__(self, name, values)
        object.__setattr__(self, "exact_files", _file_map(self.exact_files, "exact_files"))


@dataclass(frozen=True)
class VerifierOracle:
    """A trusted command, optionally augmented with files hidden from the executor."""

    name: str
    argv: tuple[str, ...]
    hidden_files: Mapping[str, str] = field(default_factory=dict)
    cwd: str = "."
    timeout_seconds: int = 30
    baseline_must_fail: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "verifier name", 128))
        object.__setattr__(self, "argv", _texts(self.argv, "verifier argv", 24))
        if not self.argv:
            raise ValueError("verifier argv must not be empty")
        object.__setattr__(self, "hidden_files", _file_map(self.hidden_files, "hidden_files"))
        object.__setattr__(self, "cwd", _relative(self.cwd, "verifier cwd") if self.cwd != "." else ".")
        if isinstance(self.timeout_seconds, bool) or not 1 <= self.timeout_seconds <= 300:
            raise ValueError("verifier timeout must be between 1 and 300 seconds")
        if not isinstance(self.baseline_must_fail, bool):
            raise TypeError("baseline_must_fail must be boolean")


@dataclass(frozen=True)
class LifecycleOracle:
    required_events: tuple[str, ...] = ()
    required_policy_events: tuple[str, ...] = ()
    require_fresh_evidence: bool = False
    require_lineage: bool = False
    reject_replayed_effects: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_events", _texts(self.required_events, "required_events"))
        object.__setattr__(
            self,
            "required_policy_events",
            _texts(self.required_policy_events, "required_policy_events"),
        )
        for name in ("require_fresh_evidence", "require_lineage", "reject_replayed_effects"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    kind: str
    generation: int = 0
    effect_id: str | None = None
    parent_event_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _text(self.event_id, "event_id", 128))
        object.__setattr__(self, "kind", _text(self.kind, "event kind", 128))
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 0:
            raise ValueError("event generation must be non-negative")
        for name in ("effect_id", "parent_event_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name, 128))


@dataclass(frozen=True)
class ScenarioExpectedOutcome:
    task_status: str
    file_contents: Mapping[str, str] = field(default_factory=dict)
    forbidden_policy_events: tuple[str, ...] = ()
    evidence_complete: bool = True
    workspace: WorkspaceOracle | None = None
    verifiers: tuple[VerifierOracle, ...] = ()
    lifecycle: LifecycleOracle = field(default_factory=LifecycleOracle)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_status", _text(self.task_status, "task_status"))
        contents = _file_map(self.file_contents, "file_contents")
        object.__setattr__(self, "file_contents", contents)
        object.__setattr__(
            self,
            "forbidden_policy_events",
            _texts(self.forbidden_policy_events, "forbidden_policy_events"),
        )
        if not isinstance(self.evidence_complete, bool):
            raise TypeError("evidence_complete must be boolean")
        workspace = self.workspace or WorkspaceOracle(exact_files=contents)
        if not isinstance(workspace, WorkspaceOracle):
            raise TypeError("workspace must be a WorkspaceOracle")
        object.__setattr__(self, "workspace", workspace)
        verifiers = tuple(self.verifiers)
        if len(verifiers) > 8 or not all(isinstance(item, VerifierOracle) for item in verifiers):
            raise ValueError("verifiers must contain trusted verifier oracles")
        if len({item.name for item in verifiers}) != len(verifiers):
            raise ValueError("verifier names must be unique")
        object.__setattr__(self, "verifiers", verifiers)
        if not isinstance(self.lifecycle, LifecycleOracle):
            raise TypeError("lifecycle must be a LifecycleOracle")


@dataclass(frozen=True)
class ScenarioPrompt:
    """Only the executor-visible portion of a fixed replay scenario."""

    identifier: str
    fixture_version: str
    user_request: str
    allowed_workspace: str
    forbidden_actions: tuple[str, ...]
    required_verifiers: tuple[str, ...]
    forbidden_verifiers: tuple[str, ...]
    max_model_turns: int
    max_tool_calls: int
    max_active_seconds: int

    def __post_init__(self) -> None:
        for name in ("identifier", "fixture_version", "user_request", "allowed_workspace"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("forbidden_actions", "required_verifiers", "forbidden_verifiers"):
            object.__setattr__(self, name, _texts(getattr(self, name), name, 32))
        for name in ("max_model_turns", "max_tool_calls", "max_active_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class Scenario:
    identifier: str
    fixture_root: Path
    user_request: str
    expected: ScenarioExpectedOutcome
    category: str = "custom"
    fixture_version: str = "fixture-v1"
    allowed_workspace: str = "."
    forbidden_actions: tuple[str, ...] = ()
    required_verifiers: tuple[str, ...] = ()
    forbidden_verifiers: tuple[str, ...] = ()
    max_model_turns: int = 12
    max_tool_calls: int = 24
    max_active_seconds: int = 300
    fixture_files: Mapping[str, str] = field(default_factory=dict)
    golden_files: Mapping[str, str] = field(default_factory=dict)
    golden_absent_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _text(self.identifier, "identifier"))
        if not isinstance(self.fixture_root, Path) or not self.fixture_root.is_dir():
            raise ValueError("fixture_root must be an existing directory")
        object.__setattr__(self, "fixture_root", self.fixture_root.resolve())
        for name in ("user_request", "fixture_version", "category"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.expected, ScenarioExpectedOutcome):
            raise TypeError("expected must be a ScenarioExpectedOutcome")
        object.__setattr__(self, "fixture_files", _file_map(self.fixture_files, "fixture_files"))
        object.__setattr__(self, "golden_files", _file_map(self.golden_files, "golden_files"))
        absent = tuple(
            _relative(value, "golden_absent_files")
            for value in _texts(self.golden_absent_files, "golden_absent_files")
        )
        object.__setattr__(self, "golden_absent_files", absent)
        for name in ("forbidden_actions", "required_verifiers", "forbidden_verifiers"):
            object.__setattr__(self, name, _texts(getattr(self, name), name, 32))

    def prompt(self) -> ScenarioPrompt:
        return ScenarioPrompt(
            self.identifier,
            self.fixture_version,
            self.user_request,
            self.allowed_workspace,
            self.forbidden_actions,
            self.required_verifiers,
            self.forbidden_verifiers,
            self.max_model_turns,
            self.max_tool_calls,
            self.max_active_seconds,
        )


@dataclass(frozen=True)
class ScenarioResult:
    task_status: str
    policy_events: tuple[str, ...] = ()
    model_turns: int = 0
    tool_calls: int = 0
    active_seconds: int = 0
    verifier_runs: tuple[str, ...] = ()
    evidence_complete: bool = False
    evidence_generation: int = 0
    lifecycle_events: tuple[LifecycleEvent, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_status", _text(self.task_status, "task_status"))
        object.__setattr__(self, "policy_events", _texts(self.policy_events, "policy_events"))
        object.__setattr__(self, "verifier_runs", _texts(self.verifier_runs, "verifier_runs", 32))
        for name in ("model_turns", "tool_calls", "active_seconds", "evidence_generation"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if not isinstance(self.evidence_complete, bool):
            raise TypeError("evidence_complete must be boolean")
        events = tuple(self.lifecycle_events)
        if len(events) > 128 or not all(isinstance(event, LifecycleEvent) for event in events):
            raise ValueError("lifecycle_events must contain typed events")
        object.__setattr__(self, "lifecycle_events", events)
