from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib as tomli
except ImportError:
    try:
        import tomli  # type: ignore[no-redef]
    except ImportError:
        tomli = None  # type: ignore[assignment]


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


def check_syntax(
    file_path: str,
    source_text: str | None = None,
    *,
    workspace_root: Path | None = None,
) -> SyntaxCheckResult:
    """Perform a fast syntax check for supported structured source files."""
    normalized = file_path.replace("\\", "/")
    if source_text is None:
        source = _read_source(file_path, normalized, workspace_root)
        if isinstance(source, SyntaxCheckResult):
            return source
        source_text = source
    if normalized.endswith(".py"):
        return _check_python(file_path, normalized, source_text)
    if normalized.endswith(".json"):
        return _check_json(file_path, source_text)
    if normalized.endswith(".toml") and tomli is not None:
        try:
            tomli.loads(source_text)
            return SyntaxCheckResult(file_path, True)
        except Exception as error:
            return SyntaxCheckResult(file_path, False, str(error))
    return SyntaxCheckResult(file_path, True)


def _read_source(
    file_path: str, normalized: str, workspace_root: Path | None
) -> str | SyntaxCheckResult:
    if workspace_root is None:
        return SyntaxCheckResult(
            file_path, True, "No content provided for syntax check"
        )
    target = workspace_root / normalized
    if not target.is_file():
        return SyntaxCheckResult(file_path, False, "File does not exist")
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return SyntaxCheckResult(
            file_path, False, f"IO error reading file: {error}"
        )


def _check_python(
    file_path: str, normalized: str, source_text: str
) -> SyntaxCheckResult:
    try:
        ast.parse(source_text, filename=normalized)
        compile(source_text, normalized, "exec")
        return SyntaxCheckResult(file_path, True)
    except (SyntaxError, IndentationError, TabError) as error:
        return SyntaxCheckResult(
            file_path,
            False,
            getattr(error, "msg", str(error)),
            error.lineno,
            error.offset,
        )
    except Exception as error:
        return SyntaxCheckResult(file_path, False, str(error))


def _check_json(file_path: str, source_text: str) -> SyntaxCheckResult:
    try:
        json.loads(source_text)
        return SyntaxCheckResult(file_path, True)
    except Exception as error:
        line = error.lineno if isinstance(error, json.JSONDecodeError) else None
        column = error.colno if isinstance(error, json.JSONDecodeError) else None
        return SyntaxCheckResult(file_path, False, str(error), line, column)
