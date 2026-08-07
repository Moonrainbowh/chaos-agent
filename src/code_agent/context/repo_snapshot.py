from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from .models import RepoEntry
from .repo_scan import ImportRef, RepoFileFacts


def publish_entries(
    records: Mapping[str, RepoFileFacts],
) -> tuple[RepoEntry, ...]:
    """Resolve cross-file facts into one deterministic snapshot entry set."""
    module_index = _module_index(records)
    return tuple(
        RepoEntry(
            path,
            records[path].symbols,
            _resolve_imports(path, records[path].imports, module_index),
            records[path].size_bytes,
        )
        for path in sorted(records, key=lambda item: (item.casefold(), item))
    )


def strip_search_text(
    records: Mapping[str, RepoFileFacts],
) -> dict[str, RepoFileFacts]:
    """Drop transient source bodies after they have entered the FTS index."""
    return {
        path: (
            replace(facts, search_text="")
            if facts.search_text
            else facts
        )
        for path, facts in records.items()
    }


def _module_index(
    records: Mapping[str, RepoFileFacts],
) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in records:
        if not path.endswith(".py"):
            continue
        module = path[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        if module:
            index.setdefault(module, path)
    return index


def _resolve_imports(
    path: str,
    refs: Sequence[ImportRef],
    index: Mapping[str, str],
) -> tuple[str, ...]:
    current = path[:-3].replace("/", ".")
    package = (
        current[: -len(".__init__")]
        if current.endswith(".__init__")
        else current.rpartition(".")[0]
    )
    dependencies: set[str] = set()
    for ref in refs:
        if ref.level:
            parts = package.split(".") if package else []
            climb = ref.level - 1
            if climb > len(parts):
                continue
            prefix = parts[: len(parts) - climb]
            base = ".".join(
                (*prefix, *filter(None, ref.module.split(".")))
            )
        else:
            base = ref.module
        candidates = [
            f"{base}.{name}".strip(".") for name in ref.names
        ]
        candidates.append(base)
        for candidate in candidates:
            if candidate in index and index[candidate] != path:
                dependencies.add(index[candidate])
    return tuple(sorted(dependencies))
