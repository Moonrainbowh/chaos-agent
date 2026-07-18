from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.files import WorkspaceFiles

from .cache import FileSignature
from .models import Symbol


_MAX_SOURCE_BYTES = 256_000
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
class ImportRef:
    module: str
    level: int = 0
    names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.module, str):
            raise TypeError("module must be text")
        if isinstance(self.level, bool) or not isinstance(self.level, int):
            raise TypeError("level must be an integer")
        if self.level < 0:
            raise ValueError("level must not be negative")
        names = tuple(self.names)
        if not all(isinstance(item, str) and item for item in names):
            raise ValueError("names must contain non-empty text")
        object.__setattr__(self, "names", names)


@dataclass(frozen=True)
class RepoFileFacts:
    path: str
    signature: FileSignature
    symbols: tuple[Symbol, ...] = field(default_factory=tuple)
    imports: tuple[ImportRef, ...] = field(default_factory=tuple)
    size_bytes: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("path must be non-empty text")
        if not isinstance(self.signature, FileSignature):
            raise TypeError("signature must be a FileSignature")
        symbols = tuple(self.symbols)
        imports = tuple(self.imports)
        if not all(isinstance(item, Symbol) for item in symbols):
            raise TypeError("symbols must contain Symbol values")
        if not all(isinstance(item, ImportRef) for item in imports):
            raise TypeError("imports must contain ImportRef values")
        if isinstance(self.size_bytes, bool) or not isinstance(
            self.size_bytes, int
        ):
            raise TypeError("size_bytes must be an integer")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "imports", imports)


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
        relative = self.files.guard.relative(absolute).as_posix()
        signature = FileSignature(metadata.st_size, metadata.st_mtime_ns)
        suffix = PurePosixPath(relative).suffix.casefold()
        if suffix not in _DECLARATION_SUFFIXES and suffix != _PYTHON_SUFFIX:
            return RepoFileFacts(
                relative,
                signature,
                size_bytes=metadata.st_size,
            )
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
        if suffix == _PYTHON_SUFFIX:
            parsed = _parse_python(relative, document.text)
            if parsed is None:
                return RepoFileFacts(
                    relative,
                    signature,
                    size_bytes=metadata.st_size,
                )
            symbols, imports = parsed
        else:
            symbols = _parse_declarations(relative, suffix, document.text)
            imports = ()
        return RepoFileFacts(
            relative,
            signature,
            symbols,
            imports,
            metadata.st_size,
        )


class _PythonSymbols(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.stack: list[str] = []
        self.symbols: list[Symbol] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_named(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_named(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_named(node, "async_function")

    def _visit_named(self, node: ast.AST, kind: str) -> None:
        name = getattr(node, "name")
        qualified = ".".join((*self.stack, name))
        self.symbols.append(Symbol(self.path, qualified, kind, node.lineno))
        self.stack.append(name)
        self.generic_visit(node)
        self.stack.pop()


def _parse_python(
    path: str, text: str
) -> tuple[tuple[Symbol, ...], tuple[ImportRef, ...]] | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, TypeError, MemoryError):
        return None
    visitor = _PythonSymbols(path)
    visitor.visit(tree)
    imports: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(ImportRef(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(
                ImportRef(
                    node.module or "",
                    node.level,
                    tuple(
                        alias.name
                        for alias in node.names
                        if alias.name != "*"
                    ),
                )
            )
    return tuple(visitor.symbols[:_MAX_SYMBOLS]), tuple(imports)


def _parse_declarations(
    path: str, suffix: str, text: str
) -> tuple[Symbol, ...]:
    symbols: list[Symbol] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
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
