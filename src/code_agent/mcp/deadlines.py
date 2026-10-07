"""Same-task deadlines, including the Python 3.10 supported runtime."""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager


if sys.version_info >= (3, 11):
    from asyncio import timeout
else:
    from async_timeout import timeout as _timeout

    @asynccontextmanager
    async def timeout(delay: float):
        """Normalize 3.10's distinct asyncio.TimeoutError to the public type.

        The backport cancels the current task; it neither starts a second SDK
        owner nor adds an AnyIO scope around persistent SDK contexts.
        """
        try:
            async with _timeout(delay):
                yield
        except asyncio.TimeoutError as error:
            raise TimeoutError from error
