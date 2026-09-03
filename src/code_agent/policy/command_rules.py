from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from code_agent.core.models import ActionRequest

from .classifier import classify_action
from .models import Capability, RiskLevel


@dataclass(frozen=True)
class ProcessRule:
    id: str
    program_path: str
    args: tuple[str, ...]
    workspace_root: str
    workspace_fingerprint: str
    network: bool
    created_at: str


@dataclass(frozen=True)
class ProcessRuleMatch:
    rule_id: str
    program_path: str


class ProcessRuleStore:
    """Persist and match exact structured-process permissions."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be a Path")
        self._path = path.resolve(strict=False)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as database, database:
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS process_permission_rules (
                    id TEXT PRIMARY KEY,
                    program_path TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    workspace_root TEXT NOT NULL,
                    workspace_fingerprint TEXT NOT NULL,
                    network INTEGER NOT NULL CHECK(network IN (0, 1)),
                    created_at TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    UNIQUE(
                        program_path, args_json, workspace_root,
                        workspace_fingerprint, network
                    )
                )
                """
            )
            database.execute("PRAGMA user_version = 1")

    def allow(
        self,
        program: str,
        args: Sequence[str],
        *,
        workspace_root: Path,
        workspace_fingerprint: str,
        network: bool = False,
    ) -> ProcessRule:
        if not isinstance(network, bool):
            raise TypeError("network must be a bool")
        checked_args = _args(args)
        root, fingerprint = _workspace(workspace_root, workspace_fingerprint)
        program_path = _resolved_program(program)
        checked_request = ActionRequest(
            "permission-rule-check",
            "run_process_v1",
            {
                "program": Path(program_path).name,
                "args": list(checked_args),
                "cwd": ".",
            },
        )
        classified = classify_action(checked_request, workspace_root)
        if classified.risk is RiskLevel.CRITICAL or classified.capabilities.intersection(
            {Capability.OUTSIDE_WORKSPACE, Capability.PROTECTED_PATH}
        ):
            raise ValueError("command cannot be saved as a permanent permission rule")
        if Capability.NETWORK in classified.capabilities and not network:
            raise ValueError("network-capable command requires --network")
        args_json = json.dumps(checked_args, ensure_ascii=False, separators=(",", ":"))
        created_at = datetime.now(timezone.utc).isoformat()
        rule_id = str(uuid.uuid4())
        with closing(self._connect()) as database, database:
            database.execute(
                """
                INSERT INTO process_permission_rules (
                    id, program_path, args_json, workspace_root,
                    workspace_fingerprint, network, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    program_path, args_json, workspace_root,
                    workspace_fingerprint, network
                ) DO UPDATE SET enabled = 1
                """,
                (
                    rule_id,
                    _normalized_path(program_path),
                    args_json,
                    root,
                    fingerprint,
                    int(network),
                    created_at,
                ),
            )
            row = database.execute(
                """
                SELECT * FROM process_permission_rules
                WHERE program_path = ? AND args_json = ?
                  AND workspace_root = ? AND workspace_fingerprint = ?
                  AND network = ?
                """,
                (
                    _normalized_path(program_path),
                    args_json,
                    root,
                    fingerprint,
                    int(network),
                ),
            ).fetchone()
        assert row is not None
        return _rule(row)

    def list(
        self, *, workspace_root: Path, workspace_fingerprint: str
    ) -> tuple[ProcessRule, ...]:
        root, fingerprint = _workspace(workspace_root, workspace_fingerprint)
        with closing(self._connect()) as database, database:
            rows = database.execute(
                """
                SELECT * FROM process_permission_rules
                WHERE workspace_root = ? AND workspace_fingerprint = ?
                  AND enabled = 1
                ORDER BY created_at, id
                """,
                (root, fingerprint),
            ).fetchall()
        return tuple(_rule(row) for row in rows)

    def revoke(
        self,
        rule_id: str,
        *,
        workspace_root: Path,
        workspace_fingerprint: str,
    ) -> ProcessRule:
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError("rule id must be non-blank text")
        root, fingerprint = _workspace(workspace_root, workspace_fingerprint)
        with closing(self._connect()) as database, database:
            rows = database.execute(
                """
                SELECT * FROM process_permission_rules
                WHERE substr(id, 1, length(?)) = ? AND workspace_root = ?
                  AND workspace_fingerprint = ? AND enabled = 1
                ORDER BY id
                """,
                (rule_id.strip(), rule_id.strip(), root, fingerprint),
            ).fetchall()
            if not rows:
                raise KeyError("permission rule was not found")
            if len(rows) != 1:
                raise ValueError("permission rule id prefix is ambiguous")
            database.execute(
                "UPDATE process_permission_rules SET enabled = 0 WHERE id = ?",
                (rows[0]["id"],),
            )
        return _rule(rows[0])

    def match(
        self,
        request: ActionRequest,
        *,
        workspace_root: Path,
        workspace_fingerprint: str,
        execution_root: Path | None = None,
    ) -> ProcessRuleMatch | None:
        if request.name.casefold() != "run_process_v1":
            return None
        arguments = request.arguments
        program, raw_args = arguments.get("program"), arguments.get("args")
        try:
            args = _args(raw_args)  # type: ignore[arg-type]
            resolved = _resolved_program(program)  # type: ignore[arg-type]
            root, fingerprint = _workspace(workspace_root, workspace_fingerprint)
            active_root = (execution_root or workspace_root).resolve(strict=False)
            cwd = _resolved_cwd(arguments.get("cwd", "."), active_root)
            cwd.relative_to(active_root)
        except (OSError, TypeError, ValueError):
            return None
        boundary_request = ActionRequest(
            request.id,
            request.name,
            {
                **request.arguments,
                # An executable normally lives outside the workspace.  Its pinned
                # identity is checked separately; only cwd/arguments are scope targets.
                "program": Path(resolved).name,
            },
        )
        classified = classify_action(boundary_request, active_root)
        if classified.risk is RiskLevel.CRITICAL or classified.capabilities.intersection(
            {Capability.OUTSIDE_WORKSPACE, Capability.PROTECTED_PATH}
        ):
            return None
        requires_network = Capability.NETWORK in classified.capabilities
        args_json = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
        with closing(self._connect()) as database, database:
            rows = database.execute(
                """
                SELECT * FROM process_permission_rules
                WHERE program_path = ? AND args_json = ?
                  AND workspace_root = ? AND workspace_fingerprint = ?
                  AND enabled = 1
                ORDER BY network ASC, created_at
                """,
                (_normalized_path(resolved), args_json, root, fingerprint),
            ).fetchall()
        for row in rows:
            if not requires_network or bool(row["network"]):
                return ProcessRuleMatch(row["id"], row["program_path"])
        return None

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self._path, timeout=5)
        database.row_factory = sqlite3.Row
        return database


def _workspace(root: Path, fingerprint: str) -> tuple[str, str]:
    if not isinstance(root, Path):
        raise TypeError("workspace_root must be a Path")
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise ValueError("workspace_fingerprint must be non-blank text")
    return _normalized_path(root.resolve(strict=False)), fingerprint.strip()


def _args(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError("args must be a sequence of strings")
    checked = tuple(values)
    if not all(isinstance(value, str) and "\0" not in value for value in checked):
        raise ValueError("args must contain strings without NUL")
    return checked


def _resolved_program(program: str) -> str:
    if not isinstance(program, str) or not program.strip() or "\0" in program:
        raise ValueError("program must be non-blank text without NUL")
    literal = Path(program.strip())
    candidate = str(literal) if literal.is_absolute() else shutil.which(program.strip())
    if candidate is None:
        raise ValueError("program could not be resolved on PATH")
    resolved = Path(candidate).resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("program must resolve to a file")
    return str(resolved)


def _resolved_cwd(value: object, root: Path) -> Path:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("cwd must be non-blank text without NUL")
    candidate = Path(value)
    return (candidate if candidate.is_absolute() else root / candidate).resolve(strict=False)


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).resolve(strict=False)))


def _rule(row: sqlite3.Row) -> ProcessRule:
    args = json.loads(row["args_json"])
    if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
        raise ValueError("stored permission rule has invalid args")
    return ProcessRule(
        row["id"],
        row["program_path"],
        tuple(args),
        row["workspace_root"],
        row["workspace_fingerprint"],
        bool(row["network"]),
        row["created_at"],
    )
__all__ = ("ProcessRule", "ProcessRuleMatch", "ProcessRuleStore")
