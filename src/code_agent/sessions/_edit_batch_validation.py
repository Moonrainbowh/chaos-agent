from __future__ import annotations

import ntpath


def validate_prepare_path_facts(
    mutation: object, operations: tuple[object, ...]
) -> None:
    _validate_endpoint_identities(operations)
    persisted = {
        _fact(
            item.path,
            item.before_existed,
            item.before_sha256,
            item.after_existed,
            item.after_sha256,
        )
        for item in mutation.paths
    }
    planned: set[tuple[object, ...]] = set()
    for operation in operations:
        if operation.case_only:
            source = operation.source
            if source is None:
                raise ValueError("case-only move requires a source")
            planned.add(
                _fact(
                    source.path,
                    source.before_existed,
                    source.before_sha256,
                    True,
                    source.before_sha256,
                )
            )
            continue
        for endpoint in (operation.source, operation.target):
            if endpoint is not None:
                planned.add(
                    _fact(
                        endpoint.path,
                        endpoint.before_existed,
                        endpoint.before_sha256,
                        endpoint.after_existed,
                        endpoint.after_sha256,
                    )
                )
    if persisted != planned:
        raise ValueError("mutation paths must match batch endpoint facts")


def _validate_endpoint_identities(operations: tuple[object, ...]) -> None:
    seen: set[str] = set()
    for operation in operations:
        endpoints = tuple(
            item
            for item in (operation.source, operation.target)
            if item is not None
        )
        identities = [ntpath.normcase(item.path) for item in endpoints]
        local = set(identities)
        if len(local) != len(identities) and not (
            operation.case_only and len(identities) == 2
        ):
            raise ValueError("overlapping operation endpoint paths")
        if seen.intersection(local):
            raise ValueError("overlapping operation endpoint paths")
        seen.update(local)


def _fact(
    path: object,
    before_existed: object,
    before_sha256: object,
    after_existed: object,
    after_sha256: object,
) -> tuple[object, ...]:
    return (
        path,
        before_existed,
        before_sha256,
        after_existed,
        after_sha256,
    )
