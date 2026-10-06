"""Bridge client cancellation to blocked task I/O without splitting its context."""
import asyncio

from code_agent.core.cancellation import CancellationError


async def cancellable_events(stream, token):
    token.raise_if_cancelled()
    queue = asyncio.Queue(maxsize=16)

    async def consume():
        try:
            async for event in stream:
                await queue.put(event)
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    worker = asyncio.create_task(consume())
    waiter = asyncio.create_task(token.wait_async())
    try:
        while True:
            pending = asyncio.create_task(queue.get())
            try:
                done, _ = await asyncio.wait((pending, worker, waiter),
                                             return_when=asyncio.FIRST_COMPLETED)
                if waiter in done:
                    worker.cancel()
                    await _settle(worker)
                    raise CancellationError(token.reason or "cancelled")
                if pending in done:
                    yield pending.result()
                elif worker in done:
                    if queue.empty():
                        worker.result()
                        return
                    yield await pending
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    finally:
        waiter.cancel()
        if not worker.done():
            worker.cancel()
        await _settle(worker)
        await asyncio.gather(waiter, return_exceptions=True)


async def _settle(worker):
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            continue
    if not worker.cancelled():
        worker.exception()
