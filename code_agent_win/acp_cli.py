from __future__ import annotations

import asyncio
import sys

from .cli import run
from .stdio import configure_windows_utf8_stdio


def main() -> int:
    configure_windows_utf8_stdio()
    return asyncio.run(run(("acp", *sys.argv[1:])))


if __name__ == "__main__":
    raise SystemExit(main())
