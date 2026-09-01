from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .models import Symbol
from .repo_paths import canonical_repo_path, logical_lines


_MAX_SYMBOLS = 200
_MAX_USES = 1_000
_MAX_CONFIGS = 100
_MAX_SKELETON_BYTES = 32 * 1024
_CONFIG_NAMES = ("config", "settings", "options", "profile")


@dataclass(frozen=True)
class ImportRef:
    module: str
    level: int = 0
    names: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()
    star: bool = False
    line: int = 0
    owner: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.module, str):
            raise TypeError("module must be text")
        if isinstance(self.level, bool) or not isinstance(self.level, int):
            raise TypeError("level must be an integer")
        if self.level < 0:
            raise ValueError("level must not be negative")
        names = tuple(self.names)
        aliases = tuple(tuple(pair) for pair in self.aliases)
        if not all(isinstance(item, str) and item for item in names):
            raise ValueError("names must contain non-empty text")
        if not all(
            len(pair) == 2 and all(isinstance(item, str) and item for item in pair)
            for pair in aliases
        ):
            raise ValueError("aliases must contain non-empty name pairs")
        if not isinstance(self.star, bool):
            raise TypeError("star must be a boolean")
        if isinstance(self.line, bool) or not isinstance(self.line, int) or self.line < 0:
            raise ValueError("line must be a non-negative integer")
        object.__setattr__(self, "names", names)
        object.__setattr__(self, "aliases", aliases)


@dataclass(frozen=True)
class PythonUse:
    owner: str
    expression: str
    line: int
    column: int
    kind: str


@dataclass(frozen=True)
class ConfigAccess:
    owner: str
    namespace: str
    key: str
    provenance: str
    operation: str
    line: int


@dataclass(frozen=True)
class PythonFileFacts:
    symbols: tuple[Symbol, ...] = field(default_factory=tuple)
    imports: tuple[ImportRef, ...] = field(default_factory=tuple)
    uses: tuple[PythonUse, ...] = field(default_factory=tuple)
    config_accesses: tuple[ConfigAccess, ...] = field(default_factory=tuple)
    literal_all: tuple[str, ...] | None = None
    dynamic_all: bool = False


@dataclass
class _Scope:
    kind: str
    owner: str
    bindings: set[str]
    imports: set[str]
    definitions: set[str]
    globals: set[str]
    nonlocals: set[str]


class _BindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.bindings: set[str] = set()
        self.imports: set[str] = set()
        self.definitions: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()
        self.assignments: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bindings.add(node.name)
        self.definitions.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bindings.add(node.name)
        self.definitions.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".", 1)[0]
            self.bindings.add(bound)
            self.imports.add(bound)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            bound = alias.asname or alias.name
            self.bindings.add(bound)
            self.imports.add(bound)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bindings.add(node.id)
            self.assignments.add(node.id)

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)


def _collect_scope(
    body: list[ast.stmt], *, kind: str, owner: str, arguments: ast.arguments | None = None
) -> _Scope:
    collector = _BindingCollector()
    for statement in body:
        collector.visit(statement)
    if arguments is not None:
        collector.bindings.update(
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
        )
        if arguments.vararg is not None:
            collector.bindings.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            collector.bindings.add(arguments.kwarg.arg)
    imports = collector.imports.difference(collector.assignments)
    return _Scope(
        kind,
        owner,
        collector.bindings,
        imports,
        collector.definitions,
        collector.globals,
        collector.nonlocals,
    )


class _SemanticVisitor(ast.NodeVisitor):
    def __init__(self, path: str, text: str, tree: ast.Module) -> None:
        self.path = path
        self.lines = logical_lines(text)
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRef] = []
        self.uses: list[PythonUse] = []
        self.configs: list[ConfigAccess] = []
        self.literal_all: tuple[str, ...] | None = None
        self.dynamic_all = False
        self.skeleton_bytes = 0
        self.scopes = [_collect_scope(tree.body, kind="module", owner="")]

    @property
    def owner(self) -> str:
        return self.scopes[-1].owner

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(
                ImportRef(
                    alias.name,
                    aliases=((alias.name, alias.asname),) if alias.asname else (),
                    line=node.lineno,
                    owner=self.owner,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = tuple(alias.name for alias in node.names if alias.name != "*")
        aliases = tuple(
            (alias.name, alias.asname)
            for alias in node.names
            if alias.name != "*" and alias.asname
        )
        self.imports.append(
            ImportRef(
                node.module or "",
                node.level,
                names,
                aliases,
                any(alias.name == "*" for alias in node.names),
                node.lineno,
                self.owner,
            )
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = self._qualified(node.name)
        self._add_symbol(node, qualified, "class")
        for base in node.bases:
            self._record_expr(base, "inherits", qualified)
        for decorator in node.decorator_list:
            self._record_expr(decorator, "decorator", qualified)
        scope = _collect_scope(node.body, kind="class", owner=qualified)
        self.scopes.append(scope)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, "async_function")

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        qualified = self._qualified(node.name)
        self._add_symbol(node, qualified, kind)
        for decorator in node.decorator_list:
            self._record_expr(decorator, "decorator", qualified)
        for annotation in _function_annotations(node):
            self._record_expr(annotation, "annotation", qualified)
        scope = _collect_scope(node.body, kind="function", owner=qualified, arguments=node.args)
        self.scopes.append(scope)
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_expr(node.annotation, "annotation", self.owner)
        if node.value is not None:
            self.visit(node.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            names = _literal_string_sequence(node.value)
            if names is None:
                self.dynamic_all = True
            else:
                self.literal_all = names
        self.visit(node.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        bindings = {
            argument.arg
            for argument in (
                *node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs
            )
        }
        if node.args.vararg is not None:
            bindings.add(node.args.vararg.arg)
        if node.args.kwarg is not None:
            bindings.add(node.args.kwarg.arg)
        self.scopes.append(_Scope("lambda", self.owner, bindings, set(), set(), set(), set()))
        self.visit(node.body)
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        self._record_config_call(node)
        expression = _expression(node.func)
        if expression and not self._expression_is_local(expression):
            self._add_use("call", expression, node, self.owner)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._record_config_subscript(node)
        self.visit(node.slice)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and self._binding_kind(node.id) not in {"local", "module"}:
            self._add_use("reference", node.id, node, self.owner)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        expression = _expression(node)
        if expression and not self._expression_is_local(expression):
            self._add_use("reference", expression, node, self.owner)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, node.elt)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, node.elt)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, node.elt)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, (node.key, node.value))

    def _visit_comprehension(self, node: ast.AST, values: ast.AST | tuple[ast.AST, ...]) -> None:
        generators = getattr(node, "generators")
        self.scopes.append(_Scope("comprehension", self.owner, set(), set(), set(), set(), set()))
        for generator in generators:
            self.visit(generator.iter)
            self.scopes[-1].bindings.update(_target_names(generator.target))
            for condition in generator.ifs:
                self.visit(condition)
        for value in values if isinstance(values, tuple) else (values,):
            self.visit(value)
        self.scopes.pop()

    def _qualified(self, name: str) -> str:
        return f"{self.owner}.{name}" if self.owner else name

    def _add_symbol(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, name: str, kind: str) -> None:
        if len(self.symbols) >= _MAX_SYMBOLS:
            return
        signature = _signature(self.lines, node)
        docstring = (ast.get_docstring(node, clean=False) or "")[:1_000]
        skeleton_bytes = len((signature + docstring).encode("utf-8"))
        if self.skeleton_bytes + skeleton_bytes > _MAX_SKELETON_BYTES:
            return
        self.skeleton_bytes += skeleton_bytes
        self.symbols.append(
            Symbol(
                self.path,
                name,
                kind,
                node.lineno,
                getattr(node, "end_lineno", node.lineno),
                signature,
                docstring,
            )
        )

    def _record_expr(self, node: ast.AST, kind: str, owner: str) -> None:
        expression = _expression(node)
        if expression:
            self._add_use(kind, expression, node, owner)

    def _add_use(self, kind: str, expression: str, node: ast.AST, owner: str) -> None:
        if len(self.uses) >= _MAX_USES:
            return
        item = PythonUse(owner, expression, node.lineno, node.col_offset, kind)
        if item not in self.uses:
            self.uses.append(item)

    def _binding_kind(self, name: str) -> str:
        module = self.scopes[0]
        for scope in reversed(self.scopes):
            if name in scope.globals:
                scope = module
            if name in scope.definitions:
                return "definition"
            if name in scope.bindings:
                if name not in scope.imports:
                    return "local" if scope.kind != "module" else "module"
            if name in scope.imports:
                return "import"
        return "unresolved"

    def _expression_is_local(self, expression: str) -> bool:
        return self._binding_kind(expression.split(".", 1)[0]) in {"local", "module"}

    def _record_config_call(self, node: ast.Call) -> None:
        expression = _expression(node.func)
        key = _literal_key(node.args[0]) if node.args else None
        if key is None or expression is None:
            return
        if expression in {"os.getenv", "os.environ.get"}:
            self._add_config("env", key, expression, "read", node)
            return
        base, _, method = expression.rpartition(".")
        name = base.split(".", 1)[0]
        if method == "get" and _is_config_name(name):
            provenance = self._config_provenance(name)
            self._add_config(f"mapping:{name}", key, provenance, "read", node)

    def _record_config_subscript(self, node: ast.Subscript) -> None:
        expression = _expression(node.value)
        key = _literal_key(node.slice)
        if expression == "os.environ" and key is not None:
            self._add_config("env", key, "os.environ[]", "read", node)
        elif expression and key is not None:
            name = expression.split(".", 1)[0]
            if _is_config_name(name):
                provenance = self._config_provenance(name)
                self._add_config(f"mapping:{name}", key, provenance, "read", node)

    def _config_provenance(self, name: str) -> str:
        owners = _owner_chain(self.owner)
        for ref in reversed(self.imports):
            if ref.owner not in owners:
                continue
            aliases = dict(ref.aliases)
            for imported in ref.names:
                if aliases.get(imported, imported) == name:
                    return f"import:{ref.level}:{ref.module}:{imported}"
            if not ref.names:
                bound = next(
                    (alias for imported, alias in ref.aliases if imported == ref.module),
                    ref.module.split(".", 1)[0],
                )
                if bound == name:
                    return f"import:{ref.level}:{ref.module}:"
        return f"local:{self.path}:{self.owner}:{name}"

    def _add_config(self, namespace: str, key: str, provenance: str, operation: str, node: ast.AST) -> None:
        if len(self.configs) >= _MAX_CONFIGS:
            return
        item = ConfigAccess(self.owner, namespace, key, provenance, operation, node.lineno)
        if item not in self.configs:
            self.configs.append(item)


def extract_python_semantics(path: str, text: str) -> PythonFileFacts:
    canonical = canonical_repo_path(path)
    if not isinstance(text, str):
        raise TypeError("text must be text")
    tree = ast.parse(text)
    visitor = _SemanticVisitor(canonical, text, tree)
    visitor.visit(tree)
    return PythonFileFacts(
        tuple(visitor.symbols),
        tuple(visitor.imports),
        tuple(visitor.uses),
        tuple(visitor.configs),
        visitor.literal_all,
        visitor.dynamic_all,
    )


def _function_annotations(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.AST, ...]:
    annotations = [
        argument.annotation
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        if argument.annotation is not None
    ]
    if node.args.vararg is not None and node.args.vararg.annotation is not None:
        annotations.append(node.args.vararg.annotation)
    if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
        annotations.append(node.args.kwarg.annotation)
    if node.returns is not None:
        annotations.append(node.returns)
    return tuple(annotations)


def _signature(lines: tuple[str, ...], node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body_line = node.body[0].lineno if node.body else node.lineno
    end = max(node.lineno, body_line - 1)
    raw = "\n".join(lines[node.lineno - 1 : end]).strip()
    if not raw and node.lineno <= len(lines):
        raw = lines[node.lineno - 1].strip()
    return raw[:512]


def _expression(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _expression(node.value)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = node.value.strip()
        return value if value and all(part.isidentifier() for part in value.split(".")) else None
    if isinstance(node, ast.Subscript):
        return _expression(node.value)
    return None


def _literal_key(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _literal_string_sequence(node: ast.AST) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values = tuple(_literal_key(item) for item in node.elts)
    if any(item is None for item in values):
        return None
    return tuple(item for item in values if item is not None)


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in node.elts:
            names.update(_target_names(item))
        return names
    return set()


def _is_config_name(name: str) -> bool:
    lowered = name.casefold()
    return any(token in lowered for token in _CONFIG_NAMES)


def _owner_chain(owner: str) -> tuple[str, ...]:
    parts = owner.split(".") if owner else []
    return tuple(".".join(parts[:index]) for index in range(len(parts), 0, -1)) + ("",)
