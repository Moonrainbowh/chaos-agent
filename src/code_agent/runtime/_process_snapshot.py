from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import psutil

from .errors import ProcessTreeTerminationError


_MAX_DISCOVERY_PASSES = 8
_CREATE_TIME_TOLERANCE_S = 1e-6
_PSUTIL_WAIT_TIMEOUT_S = 0.4


@dataclass(frozen=True)
class ProcessIdentity:
    process: psutil.Process
    create_time: float


@dataclass(frozen=True)
class FrozenTree:
    descendants: tuple[ProcessIdentity, ...]
    failures: tuple[str, ...]


def capture_process_identity(pid: int, process_api: Any) -> ProcessIdentity:
    process = process_api.Process(pid)
    return ProcessIdentity(process, process.create_time())


def resume_process_identity(
    identity: ProcessIdentity, process_api: Any
) -> None:
    process = identity.process
    try:
        current_create_time = process.create_time()
    except process_api.NoSuchProcess as error:
        raise ProcessTreeTerminationError(
            process.pid, ("process disappeared before resume",)
        ) from error
    except process_api.AccessDenied as error:
        raise ProcessTreeTerminationError(
            process.pid, ("access denied while verifying process before resume",)
        ) from error
    if not math.isclose(
        current_create_time,
        identity.create_time,
        rel_tol=0.0,
        abs_tol=_CREATE_TIME_TOLERANCE_S,
    ):
        raise ProcessTreeTerminationError(
            process.pid, ("process identity changed before resume",)
        )
    try:
        process.resume()
    except process_api.NoSuchProcess as error:
        raise ProcessTreeTerminationError(
            process.pid, ("process disappeared during resume",)
        ) from error
    except process_api.AccessDenied as error:
        raise ProcessTreeTerminationError(
            process.pid, ("access denied while resuming process",)
        ) from error


def freeze_process_tree(
    root_identity: ProcessIdentity,
    *,
    process_api: Any,
    max_processes: int,
) -> FrozenTree:
    failures: list[str] = []
    root = root_identity.process
    try:
        current_create_time = root.create_time()
    except process_api.NoSuchProcess:
        return FrozenTree(
            (), (f"root process identity PID {root.pid} no longer exists",)
        )
    except process_api.AccessDenied:
        return FrozenTree(
            (), (f"access denied while verifying root PID {root.pid}",)
        )
    if not math.isclose(
        current_create_time,
        root_identity.create_time,
        rel_tol=0.0,
        abs_tol=_CREATE_TIME_TOLERANCE_S,
    ):
        return FrozenTree(
            (),
            (
                f"root process identity changed for PID {root.pid}: "
                f"expected {root_identity.create_time}, got {current_create_time}",
            ),
        )

    captured = [root_identity]
    known = {(root.pid, root_identity.create_time)}
    try:
        root.suspend()
    except process_api.NoSuchProcess:
        failures.append(
            f"root process identity PID {root.pid} disappeared before suspension"
        )
        return FrozenTree((), tuple(failures))
    except process_api.AccessDenied:
        failures.append(f"access denied while suspending root PID {root.pid}")

    reached_limit = False
    for _ in range(_MAX_DISCOVERY_PASSES):
        added = 0
        try:
            children = root.children(recursive=True)
        except process_api.NoSuchProcess:
            failures.append(
                f"root process identity PID {root.pid} disappeared during discovery"
            )
            break
        except process_api.AccessDenied:
            failures.append(
                f"access denied while discovering children of root PID {root.pid}"
            )
            break

        for child in children:
            try:
                child_created = child.create_time()
            except process_api.NoSuchProcess:
                continue
            except process_api.AccessDenied:
                failures.append(
                    f"access denied while identifying descendant PID {child.pid}"
                )
                continue

            key = (child.pid, child_created)
            if key in known:
                continue
            if len(captured) >= max_processes:
                failures.append(f"process tree limit {max_processes} exceeded")
                reached_limit = True
                break

            identity = ProcessIdentity(child, child_created)
            known.add(key)
            captured.append(identity)
            added += 1
            try:
                child.suspend()
            except process_api.NoSuchProcess:
                captured.pop()
                known.remove(key)
                added -= 1
            except process_api.AccessDenied:
                failures.append(
                    f"access denied while suspending descendant PID {child.pid}"
                )

        if reached_limit or added == 0:
            break
    else:
        failures.append(
            f"process-tree discovery did not stabilize after {_MAX_DISCOVERY_PASSES} passes"
        )

    return FrozenTree(tuple(captured[1:]), tuple(failures))


def kill_and_wait_descendants(
    descendants: tuple[ProcessIdentity, ...],
    *,
    process_api: Any,
) -> tuple[str, ...]:
    failures: list[str] = []
    pending = [
        identity
        for identity in reversed(descendants)
        if _kill_if_matching(identity, failures, process_api)
    ]
    pending = _matching_identities(pending, failures, process_api)
    if not pending:
        return tuple(failures)

    alive = _wait_for_identities(pending, failures, process_api)
    if not alive:
        return tuple(failures)

    retry_pending = [
        identity
        for identity in alive
        if _kill_if_matching(identity, failures, process_api)
    ]
    retry_pending = _matching_identities(retry_pending, failures, process_api)
    if not retry_pending:
        return tuple(failures)

    final_alive = _wait_for_identities(retry_pending, failures, process_api)
    confirmed_alive = _matching_identities(final_alive, failures, process_api)
    if confirmed_alive:
        identities = ", ".join(
            f"{item.process.pid}@{item.create_time}" for item in confirmed_alive
        )
        failures.append(
            f"descendant processes survived two kill/wait attempts: {identities}"
        )
    return tuple(failures)


def _kill_if_matching(
    identity: ProcessIdentity,
    failures: list[str],
    process_api: Any,
) -> bool:
    if not _identity_matches(identity, failures, process_api):
        return False
    try:
        identity.process.kill()
    except process_api.NoSuchProcess:
        return False
    except process_api.AccessDenied:
        failures.append(
            f"access denied while killing descendant PID {identity.process.pid}"
        )
    return True


def _matching_identities(
    identities: Iterable[ProcessIdentity],
    failures: list[str],
    process_api: Any,
) -> list[ProcessIdentity]:
    return [
        identity
        for identity in identities
        if _identity_matches(identity, failures, process_api)
    ]


def _identity_matches(
    identity: ProcessIdentity,
    failures: list[str],
    process_api: Any,
) -> bool:
    try:
        current_create_time = identity.process.create_time()
    except process_api.NoSuchProcess:
        return False
    except process_api.AccessDenied:
        failures.append(
            f"access denied while verifying descendant PID {identity.process.pid}"
        )
        return False
    return math.isclose(
        current_create_time,
        identity.create_time,
        rel_tol=0.0,
        abs_tol=_CREATE_TIME_TOLERANCE_S,
    )


def _wait_for_identities(
    identities: list[ProcessIdentity],
    failures: list[str],
    process_api: Any,
) -> list[ProcessIdentity]:
    by_object_id = {id(item.process): item for item in identities}
    try:
        _, alive = process_api.wait_procs(
            [item.process for item in identities],
            timeout=_PSUTIL_WAIT_TIMEOUT_S,
        )
    except process_api.NoSuchProcess:
        return []
    except process_api.AccessDenied:
        failures.append("access denied while waiting for descendant processes")
        return identities
    return [
        by_object_id[id(item)]
        for item in alive
        if id(item) in by_object_id
    ]
