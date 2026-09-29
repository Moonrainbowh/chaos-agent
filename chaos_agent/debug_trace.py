"""Configure the opt-in JSONL runtime trace for the Windows host."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from code_agent.core.debug_trace import enable_trace


_HANDLER: logging.Handler | None = None


def configure_runtime_trace() -> None:
    """Persist the bounded local runtime trace unless explicitly disabled."""
    global _HANDLER
    if os.environ.get("CHAOS_DEBUG_TRACE") == "0" or _HANDLER is not None:
        return
    path = Path(os.environ.get("CHAOS_DEBUG_TRACE_PATH", "logs/runtime-trace.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("code_agent.runtime_trace")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    enable_trace()
    _HANDLER = handler
