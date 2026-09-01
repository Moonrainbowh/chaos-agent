from __future__ import annotations

from collections.abc import Mapping

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
