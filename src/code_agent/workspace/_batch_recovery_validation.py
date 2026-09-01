from __future__ import annotations

from ._batch_models import RecoveryOperation, RecoveryOperationKind
from ._secure_io import canonical_path_key


def validate_recovery_layout(
    operations: tuple[RecoveryOperation, ...],
) -> None:
    seen: set[str] = set()
    for operation in operations:
        paths = [operation.source.before.relative_path]
        if operation.destination is not None:
            paths.append(operation.destination.before.relative_path)
        keys = [canonical_path_key(path) for path in paths]
        case_alias = (
            operation.kind is RecoveryOperationKind.MOVE
            and operation.case_only
            and len(paths) == 2
            and paths[0] != paths[1]
            and keys[0] == keys[1]
        )
        if operation.case_only and not case_alias:
            raise ValueError("recovery case-only marker does not match its paths")
        if len(set(keys)) != len(keys) and not case_alias:
            raise ValueError(f"overlapping recovery path: {paths[-1]}")
        for key in set(keys):
            if key in seen:
                raise ValueError(f"overlapping recovery path: {paths[-1]}")
            seen.add(key)
