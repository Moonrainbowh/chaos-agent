from __future__ import annotations

import os
import sys
from typing import Any


def configure_windows_utf8_stdio(
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> None:
    """Make Windows console and redirected text output deterministic UTF-8."""
    if os.name != "nt":
        return
    for stream in (sys.stdout if stdout is None else stdout,
                   sys.stderr if stderr is None else stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


__all__ = ["configure_windows_utf8_stdio"]
