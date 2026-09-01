from __future__ import annotations

from pathlib import Path

from code_agent.workspace.edits import (
    BatchApplyResult,
    BatchApplyStatus,
)


class WorkspaceBatchRecoveryConflict(RuntimeError):
    """Stop startup when durable recovery cannot prove Agent ownership."""


async def recover_workspace_edit_batches(
    runtime: object,
    mutations: object,
    source_root: Path,
) -> tuple[BatchApplyResult, ...]:
    if not isinstance(source_root, Path):
        raise TypeError("source_root must be a Path")
    for_services = getattr(mutations, "for_services", None)
    services_for_root = getattr(runtime, "services_for_root", None)
    if not callable(for_services) or not callable(services_for_root):
        raise TypeError("workspace recovery dependencies are incomplete")
    results: list[BatchApplyResult] = []
    for root in _workspace_roots(runtime, source_root):
        services = services_for_root(root)
        bundle = for_services(services)
        capture = getattr(bundle, "capture", None)
        if capture is None:
            continue
        recover = getattr(capture, "recover_edit_batches", None)
        if not callable(recover):
            raise RuntimeError("workspace batch recovery is unavailable")
        recovered = await recover()
        if type(recovered) is not tuple or any(
            not isinstance(item, BatchApplyResult) for item in recovered
        ):
            raise TypeError("workspace batch recovery returned invalid results")
        results.extend(recovered)
        conflict = next(
            (
                item
                for item in recovered
                if item.status is BatchApplyStatus.PARTIAL_CONFLICT
            ),
            None,
        )
        if conflict is not None:
            raise WorkspaceBatchRecoveryConflict(
                _conflict_message(root, conflict)
            )
    return tuple(results)


def _workspace_roots(runtime: object, source_root: Path) -> tuple[Path, ...]:
    candidates = [source_root.resolve()]
    for name in ("_task_roots", "_thread_roots"):
        mapping = getattr(runtime, name, {})
        if not hasattr(mapping, "values"):
            raise TypeError(f"runtime {name} must be a mapping")
        candidates.extend(Path(value).resolve() for value in mapping.values())
    unique: dict[str, Path] = {}
    for root in candidates:
        unique.setdefault(str(root).casefold(), root)
    return tuple(unique.values())


def _conflict_message(root: Path, result: BatchApplyResult) -> str:
    details = "; ".join(
        f"{item.relative_path}: {item.reason}" for item in result.conflicts
    )
    suffix = details or result.error or "unresolved workspace drift"
    return f"workspace edit recovery conflicted at {root}: {suffix}"


__all__ = [
    "WorkspaceBatchRecoveryConflict",
    "recover_workspace_edit_batches",
]
