from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Generic, TypeVar

from code_agent.core.cancellation import CancellationToken


_T = TypeVar("_T")


@dataclass(frozen=True)
class Settled(Generic[_T]):
    value: _T | None = None
    error: BaseException | None = None
    cancellation: asyncio.CancelledError | None = None


async def ordered(awaitable: Awaitable[_T]) -> Settled[_T]:
    return await settle(asyncio.create_task(awaitable))


async def thread(function: object, *args: object) -> Settled[object]:
    return await settle(asyncio.create_task(asyncio.to_thread(function, *args)))


async def thread_until_token(
    function: object,
    *args: object,
    cancellation: CancellationToken | None,
) -> Settled[object]:
    if cancellation is None:
        return await thread(function, *args)
    cancellation.raise_if_cancelled()
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    token_waiter = asyncio.create_task(cancellation.wait_async())
    caller_cancelled: asyncio.CancelledError | None = None
    try:
        try:
            await asyncio.wait(
                (worker, token_waiter),
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError as error:
            caller_cancelled = error
        outcome = await settle(worker)
        return Settled(
            outcome.value,
            outcome.error,
            caller_cancelled or outcome.cancellation,
        )
    finally:
        if not token_waiter.done():
            token_waiter.cancel()


async def settle(task: asyncio.Task[_T]) -> Settled[_T]:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
        except BaseException:
            break
    try:
        return Settled(task.result(), cancellation=cancellation)
    except BaseException as error:
        return Settled(error=error, cancellation=cancellation)


async def value(outcome: Settled[_T]) -> _T:
    if outcome.cancellation is not None:
        raise outcome.cancellation
    if outcome.error is not None:
        raise outcome.error
    return outcome.value  # type: ignore[return-value]


def take(
    outcome: Settled[_T],
    cancellation: asyncio.CancelledError | None,
) -> tuple[_T, asyncio.CancelledError | None]:
    pending = cancellation or outcome.cancellation
    if outcome.error is not None:
        raise outcome.error
    return outcome.value, pending  # type: ignore[return-value]


__all__ = [
    "Settled",
    "ordered",
    "take",
    "thread",
    "thread_until_token",
    "value",
]
