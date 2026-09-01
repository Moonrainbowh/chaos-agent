from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.files import WorkspaceFiles

from .models import FileSignature, Symbol
from .repo_paths import canonical_repo_path, logical_lines
from .repo_python_semantics import (
    ConfigAccess,
    ImportRef,
    PythonUse,
    extract_python_semantics,
)


_MAX_SOURCE_BYTES = 256_000
_MAX_SEARCH_CHARS = 16_000
_MAX_LINE_CHARS = 4_000
_MAX_SYMBOLS = 200
_PYTHON_SUFFIX = ".py"
_DECLARATION_SUFFIXES = frozenset(
    {
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".cs",
    }
)
_JS_TYPE = re.compile(
    r"^\s*(?:(?:export|default|declare|abstract)\s+)*"
    r"(class|interface|type|enum)\s+([A-Za-z_$][\w$]*)"
)
_JS_FUNCTION = re.compile(
    r"^\s*(?:(?:export|default|declare)\s+)*"
    r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
)
_RUST_DECL = re.compile(
    r'^\s*(?:(?:pub(?:\([^)]*\))?|async|unsafe|const|extern(?:\s+"[^"]+")?)\s+)*'
    r"(struct|enum|trait|type|fn)\s+([A-Za-z_]\w*)"
)
_GO_TYPE = re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s*(struct|interface)?")
_GO_FUNC = re.compile(
    r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*(?:\[|\()"
)
_JVM_TYPE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|abstract|sealed|"
    r"final|partial|open)\s+)*(class|interface|enum|struct|record)\s+"
    r"([A-Za-z_]\w*)"
)


@dataclass(frozen=True)
class RepoFileFacts:
    path: str
    signature: FileSignature
    symbols: tuple[Symbol, ...] = field(default_factory=tuple)
    imports: tuple[ImportRef, ...] = field(default_factory=tuple)
    size_bytes: int = 0
    search_text: str = field(default="", repr=False)
    uses: tuple[PythonUse, ...] = field(default_factory=tuple)
    config_accesses: tuple[ConfigAccess, ...] = field(default_factory=tuple)
    literal_all: tuple[str, ...] | None = None
    dynamic_all: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("path must be non-empty text")
        if not isinstance(self.signature, FileSignature):
            raise TypeError("signature must be a FileSignature")
        symbols = tuple(self.symbols)
        imports = tuple(self.imports)
        uses = tuple(self.uses)
        config_accesses = tuple(self.config_accesses)
        if not all(isinstance(item, Symbol) for item in symbols):
            raise TypeError("symbols must contain Symbol values")
        if not all(isinstance(item, ImportRef) for item in imports):
            raise TypeError("imports must contain ImportRef values")
        if not all(isinstance(item, PythonUse) for item in uses):
            raise TypeError("uses must contain PythonUse values")
        if not all(isinstance(item, ConfigAccess) for item in config_accesses):
            raise TypeError("config_accesses must contain ConfigAccess values")
        if isinstance(self.size_bytes, bool) or not isinstance(
            self.size_bytes, int
        ):
            raise TypeError("size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if not isinstance(self.search_text, str):
            raise TypeError("search_text must be text")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "imports", imports)
        object.__setattr__(self, "uses", uses)
        object.__setattr__(self, "config_accesses", config_accesses)
        if self.literal_all is not None:
            object.__setattr__(self, "literal_all", tuple(self.literal_all))
        if not isinstance(self.dynamic_all, bool):
            raise TypeError("dynamic_all must be a boolean")


class RepoFileScanner:
    """Extract bounded repository facts from exactly one guarded file."""

    def __init__(self, files: WorkspaceFiles) -> None:
        if not isinstance(files, WorkspaceFiles):
            raise TypeError("files must be WorkspaceFiles")
        self.files = files

    def scan(self, path: str) -> RepoFileFacts:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be non-empty text")
        absolute = self.files.guard.resolve(path)
        metadata = absolute.stat()
        if not absolute.is_file():
            raise WorkspaceError(f"not a regular file: {absolute}")
        relative = canonical_repo_path(
            self.files.guard.relative(absolute).as_posix()
        )
        signature = FileSignature.from_stat(metadata)
        suffix = PurePosixPath(relative).suffix.casefold()
        try:
            document = self.files.read_text(
                relative, max_bytes=_MAX_SOURCE_BYTES
            )
        except (OSError, UnicodeError, WorkspaceError):
            return RepoFileFacts(
                relative,
                signature,
                size_bytes=metadata.st_size,
            )
        search_text = _sample_search_text(document.text)
        if suffix not in _DECLARATION_SUFFIXES and suffix != _PYTHON_SUFFIX:
            return RepoFileFacts(
                relative,
                signature,
                size_bytes=metadata.st_size,
                search_text=search_text,
            )
        if suffix == _PYTHON_SUFFIX:
            try:
                parsed = extract_python_semantics(relative, document.text)
            except (SyntaxError, ValueError, TypeError, MemoryError):
                return RepoFileFacts(
                    relative,
                    signature,
                    size_bytes=metadata.st_size,
                    search_text=search_text,
                )
            symbols, imports = parsed.symbols, parsed.imports
            uses, config_accesses = parsed.uses, parsed.config_accesses
            literal_all, dynamic_all = parsed.literal_all, parsed.dynamic_all
        else:
            symbols = _parse_declarations(relative, suffix, document.text)
            imports = ()
            uses, config_accesses = (), ()
            literal_all, dynamic_all = None, False
        return RepoFileFacts(
            path=relative,
            signature=signature,
            symbols=symbols,
            imports=imports,
            size_bytes=metadata.st_size,
            search_text=search_text,
            uses=uses,
            config_accesses=config_accesses,
            literal_all=literal_all,
            dynamic_all=dynamic_all,
        )


def _sample_search_text(text: str) -> str:
    if len(text) <= _MAX_SEARCH_CHARS:
        return text
    head_size = _MAX_SEARCH_CHARS * 3 // 4
    tail_size = _MAX_SEARCH_CHARS - head_size - 1
    return f"{text[:head_size]}\n{text[-tail_size:]}"


def _parse_declarations(
    path: str, suffix: str, text: str
) -> tuple[Symbol, ...]:
    symbols: list[Symbol] = []
    for line_number, line in enumerate(logical_lines(text), start=1):
        if len(line) > _MAX_LINE_CHARS:
            continue
        declarations: list[tuple[str, str]] = []
        if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
            if match := _JS_TYPE.match(line):
                declarations.append((match.group(2), match.group(1)))
            if match := _JS_FUNCTION.match(line):
                declarations.append((match.group(1), "function"))
        elif suffix == ".rs" and (match := _RUST_DECL.match(line)):
            declarations.append(
                (
                    match.group(2),
                    "function" if match.group(1) == "fn" else match.group(1),
                )
            )
        elif suffix == ".go":
            if match := _GO_TYPE.match(line):
                declarations.append(
                    (match.group(1), match.group(2) or "type")
                )
            if match := _GO_FUNC.match(line):
                declarations.append((match.group(1), "function"))
        elif suffix in {".java", ".cs"} and (match := _JVM_TYPE.match(line)):
            declarations.append((match.group(2), match.group(1)))
        symbols.extend(
            Symbol(path, name, kind, line_number)
            for name, kind in declarations
        )
        if len(symbols) >= _MAX_SYMBOLS:
            break
    return tuple(symbols[:_MAX_SYMBOLS])
