from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4_000:
        raise ValueError(f"{name} must be bounded non-blank text")
    return value


@dataclass(frozen=True)
class ScenarioExpectedOutcome:
    task_status: str
    file_contents: Mapping[str, str] = field(default_factory=dict)
    forbidden_policy_events: tuple[str, ...] = ()
    evidence_complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_status", _text(self.task_status, "task_status"))
        contents = dict(self.file_contents)
        if len(contents) > 64 or not all(isinstance(path, str) and path and isinstance(content, str) for path, content in contents.items()):
            raise ValueError("file_contents must be bounded text mappings")
        object.__setattr__(self, "file_contents", contents)
        events = tuple(self.forbidden_policy_events)
        if not all(isinstance(event, str) and event for event in events):
            raise ValueError("forbidden_policy_events must be text")
        object.__setattr__(self, "forbidden_policy_events", events)
        if not isinstance(self.evidence_complete, bool):
            raise TypeError("evidence_complete must be boolean")


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
            values = tuple(getattr(self, name))
            if len(values) > 32 or not all(isinstance(value, str) and value for value in values):
                raise ValueError(f"{name} must contain bounded text")
            object.__setattr__(self, name, values)
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
    fixture_version: str = "fixture-v1"
    allowed_workspace: str = "."
    forbidden_actions: tuple[str, ...] = ()
    required_verifiers: tuple[str, ...] = ()
    forbidden_verifiers: tuple[str, ...] = ()
    max_model_turns: int = 12
    max_tool_calls: int = 24
    max_active_seconds: int = 300
    fixture_files: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifier", _text(self.identifier, "identifier"))
        if not isinstance(self.fixture_root, Path) or not self.fixture_root.is_dir():
            raise ValueError("fixture_root must be an existing directory")
        object.__setattr__(self, "fixture_root", self.fixture_root.resolve())
        object.__setattr__(self, "user_request", _text(self.user_request, "user_request"))
        if not isinstance(self.expected, ScenarioExpectedOutcome):
            raise TypeError("expected must be a ScenarioExpectedOutcome")
        files = dict(self.fixture_files)
        if len(files) > 64 or not all(
            isinstance(path, str) and path and ".." not in Path(path).parts and not Path(path).is_absolute()
            and isinstance(content, str) for path, content in files.items()
        ):
            raise ValueError("fixture_files must be bounded relative text mappings")
        object.__setattr__(self, "fixture_files", files)

    def prompt(self) -> ScenarioPrompt:
        return ScenarioPrompt(
            self.identifier, self.fixture_version, self.user_request, self.allowed_workspace,
            self.forbidden_actions, self.required_verifiers, self.forbidden_verifiers,
            self.max_model_turns, self.max_tool_calls, self.max_active_seconds,
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_status", _text(self.task_status, "task_status"))
        events = tuple(self.policy_events)
        if not all(isinstance(event, str) and event for event in events):
            raise ValueError("policy_events must be text")
        object.__setattr__(self, "policy_events", events)
        for name in ("model_turns", "tool_calls", "active_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        verifiers = tuple(self.verifier_runs)
        if len(verifiers) > 32 or not all(isinstance(value, str) and value for value in verifiers):
            raise ValueError("verifier_runs must contain bounded text")
        object.__setattr__(self, "verifier_runs", verifiers)
        if not isinstance(self.evidence_complete, bool):
            raise TypeError("evidence_complete must be boolean")
