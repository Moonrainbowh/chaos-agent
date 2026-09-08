from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from .models import RepoRelation, Symbol
from .repo_scan import RepoFileFacts


_MAX_RELATIONS = 2_000


def resolve_semantic_graph(
    records: Mapping[str, RepoFileFacts],
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[RepoRelation, ...]]]:
    """Resolve direct static dependencies and relations for one complete fact set."""
    resolver = _Resolver(records)
    dependencies: dict[str, tuple[str, ...]] = {}
    relations: dict[str, tuple[RepoRelation, ...]] = {}
    for path in sorted(records, key=lambda item: (item.casefold(), item)):
        deps, edges = resolver.resolve_file(path, records[path])
        dependencies[path] = deps
        relations[path] = edges[:_MAX_RELATIONS]
    return dependencies, relations


class _Resolver:
    def __init__(self, records: Mapping[str, RepoFileFacts]) -> None:
        self.records = records
        module_candidates: dict[str, list[str]] = {}
        self.path_modules: dict[str, str] = {}
        self.symbols: dict[str, dict[str, Symbol]] = {}
        for path, facts in records.items():
            module = _module_name(path)
            if module:
                module_candidates.setdefault(module, []).append(path)
                self.path_modules[path] = module
            self.symbols[path] = {
                symbol.name: symbol for symbol in facts.symbols
            }
        self.module_paths = {
            module: paths[0]
            for module, paths in module_candidates.items()
            if len(paths) == 1
        }
        self.ambiguous_modules = {
            module for module, paths in module_candidates.items() if len(paths) > 1
        }
        self.configs: dict[tuple[str, str, str], list[tuple[str, object]]] = {}
        for path, facts in records.items():
            for config in facts.config_accesses:
                key = (
                    config.namespace,
                    config.key,
                    self._normalized_config_provenance(path, config.provenance),
                )
                self.configs.setdefault(key, []).append((path, config))
        self.exports = self._build_exports()

    def _build_exports(self) -> dict[tuple[str, str], tuple[str, Symbol]]:
        exports: dict[tuple[str, str], tuple[str, Symbol]] = {}
        for path, by_name in self.symbols.items():
            module = self.path_modules.get(path, "")
            if not module or module in self.ambiguous_modules:
                continue
            for name, symbol in by_name.items():
                if "." not in name:
                    exports[(module, name)] = (path, symbol)
        for _ in range(8):
            changed = False
            for path, facts in self.records.items():
                if not path.endswith("/__init__.py"):
                    continue
                if facts.dynamic_all:
                    continue
                for ref in facts.imports:
                    if ref.star:
                        continue
                    module = self._absolute_module(path, ref)
                    for name in ref.names:
                        exported = ref.aliases and dict(ref.aliases).get(name) or name
                        if facts.literal_all is not None and exported not in facts.literal_all:
                            continue
                        target = exports.get((module, name))
                        key = (self.path_modules.get(path, ""), exported)
                        if target is not None and exports.get(key) != target:
                            exports[key] = target
                            changed = True
            if not changed:
                break
        return exports

    def resolve_file(
        self, path: str, facts: RepoFileFacts
    ) -> tuple[tuple[str, ...], tuple[RepoRelation, ...]]:
        bindings: dict[str, dict[str, tuple[str, Symbol | None, str]]] = {}
        dependencies: set[str] = set()
        edges: list[RepoRelation] = []
        for ref in facts.imports:
            scoped_bindings = bindings.setdefault(ref.owner, {})
            module = self._absolute_module(path, ref)
            module_path = self.module_paths.get(module)
            if module_path and module_path != path:
                dependencies.add(module_path)
                edges.append(_edge("import", "", ref.line, module_path, None, "exact", "explicit module import"))
            if ref.star:
                continue
            aliases = dict(ref.aliases)
            if ref.names:
                for name in ref.names:
                    bound = aliases.get(name, name)
                    target = self.exports.get((module, name))
                    submodule_path = self.module_paths.get(f"{module}.{name}".strip("."))
                    if target is not None:
                        target_path, symbol = target
                        scoped_bindings[bound] = (target_path, symbol, "exact")
                        dependencies.add(target_path)
                        edges.append(_edge("import", "", ref.line, target_path, symbol, "exact", "explicit imported symbol"))
                    elif submodule_path is not None:
                        scoped_bindings[bound] = (submodule_path, None, "exact")
                        dependencies.add(submodule_path)
            else:
                for imported, alias in ref.aliases:
                    target_path = self.module_paths.get(imported)
                    if target_path:
                        scoped_bindings[alias] = (target_path, None, "exact")
                if module_path:
                    bound = next((alias for imported, alias in ref.aliases if imported == ref.module), ref.module.split(".", 1)[0])
                    if bound:
                        scoped_bindings[bound] = (module_path, None, "exact")

        local = self.symbols.get(path, {})
        for use in facts.uses:
            resolved = self._resolve_use(
                use.expression, use.owner, path, local, bindings
            )
            if resolved is None:
                continue
            target_path, symbol, resolution = resolved
            kind = use.kind if use.kind in {"call", "inherits"} else "reference"
            edges.append(_edge(kind, use.owner, use.line, target_path, symbol, resolution, f"{use.kind} static resolution"))
            if kind == "call":
                edges.append(_edge("reference", use.owner, use.line, target_path, symbol, resolution, "call target reference"))
            if target_path != path:
                dependencies.add(target_path)
        for config in facts.config_accesses:
            provenance = self._normalized_config_provenance(
                path, config.provenance
            )
            edges.append(
                RepoRelation(
                    "config",
                    config.owner,
                    config.line,
                    resolution="exact",
                    reason=config.operation,
                    config_namespace=config.namespace,
                    config_key=config.key,
                    config_provenance=provenance,
                )
            )
            peerable = config.namespace == "env" or provenance.startswith("import:")
            peers = sorted(
                self.configs.get(
                    (config.namespace, config.key, provenance), ()
                ),
                key=lambda item: (item[0].casefold(), item[0], getattr(item[1], "line")),
            )[:16] if peerable else ()
            for target_path, target in peers:
                if target_path == path:
                    continue
                edges.append(RepoRelation(
                    "config",
                    config.owner,
                    config.line,
                    target_path=target_path,
                    target_symbol=getattr(target, "owner"),
                    target_line=getattr(target, "line"),
                    target_end_line=getattr(target, "line"),
                    resolution="exact",
                    reason="matching namespace/key/provenance",
                    config_namespace=config.namespace,
                    config_key=config.key,
                    config_provenance=provenance,
                ))
                dependencies.add(target_path)
        unique = tuple(sorted(set(edges), key=_relation_key))
        return tuple(sorted(dependencies)), unique

    def _resolve_use(
        self,
        expression: str,
        owner: str,
        path: str,
        local: Mapping[str, Symbol],
        bindings: Mapping[str, Mapping[str, tuple[str, Symbol | None, str]]],
    ) -> tuple[str, Symbol, str] | None:
        if expression in local:
            return path, local[expression], "exact"
        for scope_owner in _owner_chain(owner):
            qualified = f"{scope_owner}.{expression}" if scope_owner else expression
            if qualified in local:
                return path, local[qualified], "exact"
        first, _, rest = expression.partition(".")
        bound = next(
            (
                bindings[scope_owner][first]
                for scope_owner in _owner_chain(owner)
                if first in bindings.get(scope_owner, {})
            ),
            None,
        )
        if bound is None:
            return None
        target_path, target_symbol, resolution = bound
        if not rest:
            return (target_path, target_symbol, resolution) if target_symbol else None
        candidates = self.symbols.get(target_path, {})
        symbol = candidates.get(rest) or candidates.get(rest.split(".")[-1])
        if symbol is not None:
            return target_path, symbol, resolution
        return None

    def _absolute_module(self, path: str, ref: object) -> str:
        module = getattr(ref, "module")
        level = getattr(ref, "level")
        return self._absolute_module_parts(path, module, level)

    def _absolute_module_parts(self, path: str, module: str, level: int) -> str:
        if not level:
            return module
        current = self.path_modules.get(path, "")
        package = current if path.endswith("/__init__.py") else current.rpartition(".")[0]
        parts = package.split(".") if package else []
        climb = level - 1
        if climb > len(parts):
            return ""
        prefix = parts[: len(parts) - climb]
        return ".".join((*prefix, *filter(None, module.split("."))))

    def _normalized_config_provenance(self, path: str, provenance: str) -> str:
        if not provenance.startswith("import:"):
            return provenance
        try:
            level_text, module, name = provenance[len("import:") :].split(":", 2)
            level = int(level_text)
        except (TypeError, ValueError):
            return provenance
        absolute = self._absolute_module_parts(path, module, level)
        return f"import:{absolute}:{name}"


def _module_name(path: str) -> str:
    if not path.endswith(".py"):
        return ""
    module_path = path[:-3]
    if module_path.startswith("src/"):
        module_path = module_path[4:]
    module = module_path.replace("/", ".")
    return module[: -len(".__init__")] if module.endswith(".__init__") else module


def _edge(
    kind: str,
    owner: str,
    line: int,
    target_path: str,
    symbol: Symbol | None,
    resolution: str,
    reason: str,
) -> RepoRelation:
    return RepoRelation(
        kind,
        owner,
        line,
        target_path,
        "" if symbol is None else symbol.name,
        0 if symbol is None else symbol.line,
        0 if symbol is None or symbol.end_line is None else symbol.end_line,
        resolution,
        reason,
    )


def _relation_key(edge: RepoRelation) -> tuple[object, ...]:
    return (
        edge.kind,
        edge.source_line,
        edge.source_symbol,
        edge.target_path.casefold(),
        edge.target_symbol,
        edge.config_namespace,
        edge.config_key,
        edge.reason,
    )


def _owner_chain(owner: str) -> tuple[str, ...]:
    parts = owner.split(".") if owner else []
    return tuple(".".join(parts[:index]) for index in range(len(parts), 0, -1)) + ("",)


def _is_test_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    name = parts[-1]
    if "tests" in parts or "test" in parts:
        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or name.endswith("_tests.py")
            or (name.endswith(".py") and "test" in name)
        )
    return name.startswith("test_") or name.endswith("_test.py")


class UnifiedSemanticGraph:
    """Shared code semantic graph serving Context, Verification, Risk, Review, and Refactoring.

    Acts as the single cognitive foundation (World Model) for code agent operations:
    - Context Selection: Repo Map and dependency slicing
    - Test Impact Analysis: reverse traversal of import/call topology to find affected tests
    - Change Risk: centrality, fan-in in-degree, and subsystem risk evaluation
    - Review Scope: blast radius calculation (changed files + consumers + tests)
    - Refactor Planning: cycle-safe topological dependency ordering
    - Symbol-level caller lookup across repository relations
    """

    def __init__(
        self,
        nodes: Sequence[str],
        dependencies: Mapping[str, Sequence[str]],
        relations: Mapping[str, Sequence[RepoRelation]],
        symbols: Mapping[str, Mapping[str, Symbol]] | None = None,
    ) -> None:
        self._nodes: tuple[str, ...] = tuple(sorted(set(nodes)))
        self._dependencies: dict[str, tuple[str, ...]] = {
            k: tuple(sorted(set(v))) for k, v in dependencies.items()
        }
        self._relations: dict[str, tuple[RepoRelation, ...]] = {
            k: tuple(v) for k, v in relations.items()
        }
        self._symbols: dict[str, dict[str, Symbol]] = (
            {k: dict(v) for k, v in symbols.items()} if symbols is not None else {}
        )
        # Compute reverse dependencies (dependents): who depends on path X?
        dependents: dict[str, set[str]] = {node: set() for node in self._nodes}
        for source, targets in self._dependencies.items():
            for target in targets:
                dependents.setdefault(target, set()).add(source)
        self._dependents: dict[str, tuple[str, ...]] = {
            k: tuple(sorted(v)) for k, v in dependents.items()
        }

    @property
    def nodes(self) -> tuple[str, ...]:
        return self._nodes

    @property
    def dependencies(self) -> dict[str, tuple[str, ...]]:
        return self._dependencies

    @property
    def dependents(self) -> dict[str, tuple[str, ...]]:
        return self._dependents

    @property
    def relations(self) -> dict[str, tuple[RepoRelation, ...]]:
        return self._relations

    @property
    def symbols(self) -> dict[str, dict[str, Symbol]]:
        return self._symbols

    @classmethod
    def from_records(cls, records: Mapping[str, RepoFileFacts]) -> "UnifiedSemanticGraph":
        dependencies, relations = resolve_semantic_graph(records)
        symbols = {
            path: {s.name: s for s in facts.symbols}
            for path, facts in records.items()
        }
        return cls(
            nodes=tuple(records.keys()),
            dependencies=dependencies,
            relations=relations,
            symbols=symbols,
        )

    @classmethod
    def from_entries(cls, entries: Sequence[object]) -> "UnifiedSemanticGraph":
        nodes: list[str] = []
        dependencies: dict[str, list[str]] = {}
        relations: dict[str, list[RepoRelation]] = {}
        symbols: dict[str, dict[str, Symbol]] = {}
        for entry in entries:
            path = getattr(entry, "path", "")
            if not path:
                continue
            nodes.append(path)
            dependencies[path] = list(getattr(entry, "dependencies", ()))
            relations[path] = list(getattr(entry, "relations", ()))
            symbols[path] = {s.name: s for s in getattr(entry, "symbols", ())}
        return cls(
            nodes=nodes,
            dependencies=dependencies,
            relations=relations,
            symbols=symbols,
        )

    def get_dependencies(self, path: str) -> tuple[str, ...]:
        """Return direct forward dependencies of a file."""
        return self._dependencies.get(path, ())

    def get_dependents(self, path: str) -> tuple[str, ...]:
        """Return direct reverse dependencies (dependents) of a file."""
        return self._dependents.get(path, ())

    def get_transitive_dependents(
        self, paths: Sequence[str], max_depth: int = 5
    ) -> tuple[str, ...]:
        """Return all downstream files that transitively depend on the given paths."""
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(p, 0) for p in paths]
        while queue:
            current, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for dependent in self._dependents.get(current, ()):
                if dependent not in visited:
                    visited.add(dependent)
                    queue.append((dependent, depth + 1))
        return tuple(sorted(visited))

    def get_transitive_dependencies(
        self, paths: Sequence[str], max_depth: int = 5
    ) -> tuple[str, ...]:
        """Return all upstream prerequisites that the given paths transitively depend on."""
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(p, 0) for p in paths]
        while queue:
            current, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for dep in self._dependencies.get(current, ()):
                if dep not in visited:
                    visited.add(dep)
                    queue.append((dep, depth + 1))
        return tuple(sorted(visited))

    def find_impacted_tests(
        self, changed_files: Sequence[str], max_depth: int = 4
    ) -> tuple[str, ...]:
        """Test Impact Analysis: reverse traverse graph to find all affected test files."""
        impacted: set[str] = set()
        for f in changed_files:
            norm = f.replace("\\", "/")
            if _is_test_file(norm):
                impacted.add(norm)

        downstream = self.get_transitive_dependents(changed_files, max_depth=max_depth)
        for dep in downstream:
            if _is_test_file(dep):
                impacted.add(dep)

        node_set = set(self._nodes)
        for f in changed_files:
            norm = f.replace("\\", "/")
            stem = PurePosixPath(norm).stem
            candidates = (
                f"tests/test_{stem}.py",
                f"tests/{stem}_test.py",
                f"{norm.rsplit('/', 1)[0]}/tests/test_{stem}.py" if "/" in norm else f"tests/test_{stem}.py",
            )
            for cand in candidates:
                if cand in node_set:
                    impacted.add(cand)

        return tuple(sorted(impacted))

    def evaluate_risk(self, changed_files: Sequence[str]) -> tuple[str, str]:
        """Change Risk: determine risk tier based on file patterns and graph centrality."""
        from code_agent.verification.planner import RiskTier, classify_risk

        tier, reason = classify_risk(changed_files)
        if tier is RiskTier.CRITICAL:
            return tier.value, reason

        max_in_degree = 0
        high_impact_file = ""
        for f in changed_files:
            norm = f.replace("\\", "/")
            in_deg = len(self._dependents.get(norm, ()))
            if in_deg > max_in_degree:
                max_in_degree = in_deg
                high_impact_file = norm

        if max_in_degree >= 8:
            return (
                RiskTier.HIGH.value,
                f"High fan-in centrality ({max_in_degree} downstream dependents on '{high_impact_file}')",
            )

        return tier.value, reason

    def get_review_scope(
        self, changed_files: Sequence[str], max_fanout: int = 20
    ) -> tuple[str, ...]:
        """Review Scope: Blast radius calculation for code review."""
        scope: set[str] = set(changed_files)
        for f in changed_files:
            norm = f.replace("\\", "/")
            for dependent in self._dependents.get(norm, ()):
                scope.add(dependent)
                if len(scope) >= max_fanout:
                    break
        impacted_tests = self.find_impacted_tests(changed_files)
        scope.update(impacted_tests[:5])
        return tuple(sorted(scope))

    def plan_refactor_order(self, files: Sequence[str]) -> tuple[str, ...]:
        """Refactor Planning: Cycle-safe topological sort (foundation -> consumer -> test)."""
        norm_files = [f.replace("\\", "/") for f in files]
        file_set = set(norm_files)
        in_set_prereqs: dict[str, set[str]] = {
            f: set(self.get_transitive_dependencies([f])).intersection(file_set)
            for f in norm_files
        }
        prereq_count: dict[str, int] = {f: len(in_set_prereqs[f]) for f in norm_files}
        queue = sorted([f for f in norm_files if prereq_count[f] == 0])
        ordered: list[str] = []

        while queue:
            current = queue.pop(0)
            ordered.append(current)
            for node in sorted(in_set_prereqs.keys()):
                if current in in_set_prereqs[node]:
                    in_set_prereqs[node].remove(current)
                    prereq_count[node] -= 1
                    if prereq_count[node] == 0 and node not in ordered and node not in queue:
                        queue.append(node)

        remaining = [f for f in norm_files if f not in ordered]
        ordered.extend(sorted(remaining))
        return tuple(ordered)

    def find_symbol_callers(
        self, target_path: str, symbol_name: str
    ) -> tuple[RepoRelation, ...]:
        """Symbol-level caller lookup across all repository relations."""
        callers: list[RepoRelation] = []
        for relations in self._relations.values():
            for rel in relations:
                if rel.target_path == target_path and rel.target_symbol == symbol_name:
                    callers.append(rel)
        return tuple(callers)
