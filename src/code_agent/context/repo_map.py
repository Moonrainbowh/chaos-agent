from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Sequence

from code_agent.workspace.errors import WorkspaceError
from code_agent.workspace.files import WorkspaceFiles

from .errors import RepoMapError
from .models import ContextConfig, RepoEntry, Symbol
from .tokens import estimate_tokens, truncate_to_tokens


_MAX_SOURCE_BYTES = 256_000
_MAX_LINE_CHARS = 4_000
_MAX_SYMBOLS = 200
_PYTHON_SUFFIX = ".py"
_DECLARATION_SUFFIXES = frozenset(
    {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java", ".cs"}
)
_JS_TYPE = re.compile(
    r"^\s*(?:(?:export|default|declare|abstract)\s+)*(class|interface|type|enum)\s+([A-Za-z_$][\w$]*)"
)
_JS_FUNCTION = re.compile(
    r"^\s*(?:(?:export|default|declare)\s+)*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
)
_RUST_DECL = re.compile(
    r'^\s*(?:(?:pub(?:\([^)]*\))?|async|unsafe|const|extern(?:\s+"[^"]+")?)\s+)*(struct|enum|trait|type|fn)\s+([A-Za-z_]\w*)'
)
_GO_TYPE = re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s*(struct|interface)?")
_GO_FUNC = re.compile(
    r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*(?:\[|\()"
)
_JVM_TYPE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|abstract|sealed|final|partial|open)\s+)*(class|interface|enum|struct|record)\s+([A-Za-z_]\w*)"
)
_QUERY_TOKEN = re.compile(r"[\w.-]+", re.UNICODE)


@dataclass(frozen=True)
class _ImportRef:
    module: str
    level: int = 0
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ScannedFile:
    path: str
    symbols: tuple[Symbol, ...]
    imports: tuple[_ImportRef, ...]
    size_bytes: int


class RepoMapBuilder:
    """Build a bounded clean-room map, not a complete language parser."""

    def __init__(self, files: WorkspaceFiles, config: ContextConfig) -> None:
        if not isinstance(files, WorkspaceFiles):
            raise TypeError("files must be WorkspaceFiles")
        if not isinstance(config, ContextConfig):
            raise TypeError("config must be a ContextConfig")
        if files.guard.root != config.workspace_root:
            raise ValueError("workspace files root must match config.workspace_root")
        self.files = files
        self.config = config

    def build(
        self, query: str = "", touched_files: Sequence[str] = ()
    ) -> tuple[RepoEntry, ...]:
        if not isinstance(query, str):
            raise TypeError("query must be text")
        if any(not isinstance(path, str) or not path for path in touched_files):
            raise ValueError("touched_files must contain non-empty paths")
        scanned = self._scan()
        module_index = _module_index(item.path for item in scanned)
        entries = tuple(
            RepoEntry(
                path=item.path,
                symbols=item.symbols,
                dependencies=_resolve_imports(item.path, item.imports, module_index),
                size_bytes=item.size_bytes,
            )
            for item in scanned
        )
        return _rank(entries, query, touched_files)

    def render(
        self,
        query: str,
        touched_files: Sequence[str],
        token_budget: int,
    ) -> str:
        if isinstance(token_budget, bool) or not isinstance(token_budget, int):
            raise TypeError("token_budget must be an integer")
        if token_budget < 0:
            raise ValueError("token_budget must not be negative")
        if token_budget == 0:
            return ""
        chunks: list[str] = []
        used = 0
        for entry in self.build(query, touched_files):
            chunk = _render_entry(entry)
            separator = "\n" if chunks else ""
            cost = estimate_tokens(separator + chunk)
            if used + cost <= token_budget:
                chunks.append(chunk)
                used += cost
                continue
            if not chunks:
                return truncate_to_tokens(chunk, token_budget)
            break
        rendered = "\n".join(chunks)
        return truncate_to_tokens(rendered, token_budget)

    def _scan(self) -> tuple[_ScannedFile, ...]:
        try:
            paths = self.files.list_files(
                max_entries=self.config.repo_scan,
                max_scanned_entries=max(1_000, self.config.repo_scan * 20),
            )
        except (OSError, WorkspaceError) as error:
            raise RepoMapError("bounded repository scan failed") from error
        return tuple(self._scan_file(path) for path in paths)

    def _scan_file(self, path: str) -> _ScannedFile:
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix not in _DECLARATION_SUFFIXES and suffix != _PYTHON_SUFFIX:
            return _ScannedFile(path, (), (), self._file_size(path))
        try:
            document = self.files.read_text(path, max_bytes=_MAX_SOURCE_BYTES)
            size = len(document.text.encode("utf-8"))
        except (OSError, UnicodeError, WorkspaceError):
            return _ScannedFile(path, (), (), self._file_size(path))
        if suffix == _PYTHON_SUFFIX:
            symbols, imports = _parse_python(path, document.text)
        else:
            symbols, imports = _parse_declarations(path, suffix, document.text), ()
        return _ScannedFile(path, symbols, imports, size)

    def _file_size(self, path: str) -> int:
        try:
            return self.files.guard.resolve(path).stat().st_size
        except (OSError, WorkspaceError):
            return 0


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
) -> tuple[tuple[Symbol, ...], tuple[_ImportRef, ...]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, TypeError, MemoryError):
        return (), ()
    visitor = _PythonSymbols(path)
    visitor.visit(tree)
    imports: list[_ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(_ImportRef(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(
                _ImportRef(
                    node.module or "",
                    node.level,
                    tuple(alias.name for alias in node.names if alias.name != "*"),
                )
            )
    return tuple(visitor.symbols[:_MAX_SYMBOLS]), tuple(imports)


def _parse_declarations(path: str, suffix: str, text: str) -> tuple[Symbol, ...]:
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
            declarations.append((match.group(2), "function" if match.group(1) == "fn" else match.group(1)))
        elif suffix == ".go":
            if match := _GO_TYPE.match(line):
                declarations.append((match.group(1), match.group(2) or "type"))
            if match := _GO_FUNC.match(line):
                declarations.append((match.group(1), "function"))
        elif suffix in {".java", ".cs"} and (match := _JVM_TYPE.match(line)):
            declarations.append((match.group(2), match.group(1)))
        symbols.extend(Symbol(path, name, kind, line_number) for name, kind in declarations)
        if len(symbols) >= _MAX_SYMBOLS:
            break
    return tuple(symbols[:_MAX_SYMBOLS])


def _module_index(paths: Iterable[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in paths:
        if not path.endswith(".py"):
            continue
        module = path[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        if module:
            index.setdefault(module, path)
    return index


def _resolve_imports(
    path: str, refs: Sequence[_ImportRef], index: dict[str, str]
) -> tuple[str, ...]:
    current = path[:-3].replace("/", ".")
    package = current[: -len(".__init__")] if current.endswith(".__init__") else current.rpartition(".")[0]
    dependencies: set[str] = set()
    for ref in refs:
        if ref.level:
            parts = package.split(".") if package else []
            climb = ref.level - 1
            if climb > len(parts):
                continue
            prefix = parts[: len(parts) - climb]
            base = ".".join((*prefix, *filter(None, ref.module.split("."))))
        else:
            base = ref.module
        candidates = [f"{base}.{name}".strip(".") for name in ref.names]
        candidates.append(base)
        for candidate in candidates:
            if candidate in index and index[candidate] != path:
                dependencies.add(index[candidate])
    return tuple(sorted(dependencies))


def _rank(
    entries: Sequence[RepoEntry], query: str, touched_files: Sequence[str]
) -> tuple[RepoEntry, ...]:
    indegree = {entry.path: 0 for entry in entries}
    for entry in entries:
        for dependency in entry.dependencies:
            if dependency in indegree:
                indegree[dependency] += 1
    tokens = tuple(dict.fromkeys(_QUERY_TOKEN.findall(query.casefold())))
    touched = {path.replace("\\", "/").removeprefix("./").casefold() for path in touched_files}

    def score(entry: RepoEntry) -> int:
        path = entry.path.casefold()
        value = indegree[entry.path] * 2
        if path in touched:
            value += 100
        for token in tokens:
            if token == path:
                value += 30
            elif token in path:
                value += 12
            for symbol in entry.symbols:
                name = symbol.name.casefold()
                value += 35 if token == name else 20 if token in name else 0
        return value

    return tuple(sorted(entries, key=lambda entry: (-score(entry), entry.path.casefold(), entry.path)))


def _render_entry(entry: RepoEntry) -> str:
    lines = [entry.path]
    if entry.symbols:
        lines.append("  " + ", ".join(f"{item.kind}:{item.name}:{item.line}" for item in entry.symbols))
    if entry.dependencies:
        lines.append("  deps:" + ",".join(entry.dependencies))
    return "\n".join(lines)
