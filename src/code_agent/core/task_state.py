from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Optional, Sequence, cast

from ._json import JSONValue


_MAX_VALUES = 32
_MAX_STRING_LENGTH = 1_024


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    if len(value) > _MAX_STRING_LENGTH:
        raise ValueError(f"{label} must be at most {_MAX_STRING_LENGTH} characters")
    return value


def _texts(values: object, label: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise TypeError(f"{label} must be a sequence")
    result = tuple(_text(value, label) for value in values)
    if len(result) > _MAX_VALUES:
        raise ValueError(f"{label} must contain at most {_MAX_VALUES} values")
    return result


def _append(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    if value in values:
        return values
    return (values + (value,))[-_MAX_VALUES:]


@dataclass(frozen=True)
class CommandFact:
    command: str
    returncode: Optional[int]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", _text(self.command, "command"))
        if self.returncode is not None and (
            isinstance(self.returncode, bool) or not isinstance(self.returncode, int)
        ):
            raise TypeError("returncode must be an integer or None")
        object.__setattr__(self, "reason", _text(self.reason, "reason"))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> CommandFact:
        return cls(
            command=cast(str, data["command"]),
            returncode=cast(Optional[int], data.get("returncode")),
            reason=cast(str, data["reason"]),
        )


@dataclass(frozen=True)
class TaskStateUpdate:
    verified_facts: tuple[str, ...] = ()
    working_notes: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("verified_facts", "working_notes", "open_questions"):
            object.__setattr__(self, name, _texts(getattr(self, name), name))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "verified_facts": list(self.verified_facts),
            "working_notes": list(self.working_notes),
            "open_questions": list(self.open_questions),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TaskStateUpdate:
        return cls(
            verified_facts=cast(Sequence[str], data.get("verified_facts", ())),
            working_notes=cast(Sequence[str], data.get("working_notes", ())),
            open_questions=cast(Sequence[str], data.get("open_questions", ())),
        )


@dataclass(frozen=True)
class TaskState:
    objective: str = ""
    files_read: tuple[str, ...] = ()
    files_changed: tuple[str, ...] = ()
    failed_commands: tuple[CommandFact, ...] = ()
    verified_facts: tuple[str, ...] = ()
    working_notes: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "objective", _text(self.objective, "objective", allow_empty=True))
        for name in (
            "files_read", "files_changed", "verified_facts", "working_notes",
            "open_questions",
        ):
            object.__setattr__(self, name, _texts(getattr(self, name), name))
        commands = tuple(self.failed_commands)
        if not all(isinstance(item, CommandFact) for item in commands):
            raise TypeError("failed_commands must contain CommandFact values")
        if len(commands) > _MAX_VALUES:
            raise ValueError(f"failed_commands must contain at most {_MAX_VALUES} values")
        object.__setattr__(self, "failed_commands", commands)

    @classmethod
    def empty(cls) -> TaskState:
        return cls()

    def with_update(self, update: TaskStateUpdate) -> TaskState:
        if not isinstance(update, TaskStateUpdate):
            raise TypeError("update must be a TaskStateUpdate")
        return replace(
            self,
            verified_facts=_merge(self.verified_facts, update.verified_facts),
            working_notes=_merge(self.working_notes, update.working_notes),
            open_questions=_merge(self.open_questions, update.open_questions),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "objective": self.objective,
            "files_read": list(self.files_read),
            "files_changed": list(self.files_changed),
            "failed_commands": [item.to_dict() for item in self.failed_commands],
            "verified_facts": list(self.verified_facts),
            "working_notes": list(self.working_notes),
            "open_questions": list(self.open_questions),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> TaskState:
        commands = cast(Sequence[Mapping[str, object]], data.get("failed_commands", ()))
        return cls(
            objective=cast(str, data.get("objective", "")),
            files_read=cast(Sequence[str], data.get("files_read", ())),
            files_changed=cast(Sequence[str], data.get("files_changed", ())),
            failed_commands=tuple(CommandFact.from_dict(item) for item in commands),
            verified_facts=cast(Sequence[str], data.get("verified_facts", ())),
            working_notes=cast(Sequence[str], data.get("working_notes", ())),
            open_questions=cast(Sequence[str], data.get("open_questions", ())),
        )


def reduce_task_state(state: TaskState, request: object, result: object) -> TaskState:
    """Derive durable facts only from completed workspace and command actions."""
    if not isinstance(state, TaskState):
        raise TypeError("state must be a TaskState")
    name = getattr(request, "name", None)
    arguments = getattr(request, "arguments", None)
    is_error = getattr(result, "is_error", None)
    if not isinstance(name, str) or not isinstance(arguments, Mapping) or not isinstance(is_error, bool):
        raise TypeError("request and result must be action values")
    path = arguments.get("path")
    if not is_error and name == "read_file" and isinstance(path, str):
        return replace(
            state,
            files_read=_append(state.files_read, path),
            verified_facts=_append(state.verified_facts, f"Read file: {path}"),
        )
    if not is_error and name in {"write_file", "replace_text"} and isinstance(path, str):
        return replace(
            state,
            files_changed=_append(state.files_changed, path),
            verified_facts=_append(state.verified_facts, f"Changed file: {path}"),
        )
    if name == "run_command" and isinstance(arguments.get("command"), str):
        returncode = _returncode(result)
        if is_error or (returncode is not None and returncode != 0):
            fact = CommandFact(arguments["command"], returncode, "command failed")
            if fact not in state.failed_commands:
                return replace(state, failed_commands=(state.failed_commands + (fact,))[-_MAX_VALUES:])
    return state


def _returncode(result: object) -> Optional[int]:
    metadata = getattr(result, "metadata", {})
    output = getattr(result, "output", {})
    for source in (metadata, output):
        if isinstance(source, Mapping):
            value = source.get("returncode")
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            return value
    return None


def _merge(existing: tuple[str, ...], added: tuple[str, ...]) -> tuple[str, ...]:
    result = existing
    for value in added:
        result = _append(result, value)
    return result
