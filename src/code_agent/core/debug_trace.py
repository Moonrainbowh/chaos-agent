"""Opt-in, structured runtime tracing with bounded, safe fields."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Mapping


_LOGGER = logging.getLogger("code_agent.runtime_trace")
_CONTEXT: ContextVar[dict[str, object]] = ContextVar("runtime_trace_context", default={})
_ENABLED = False


def enable_trace() -> None:
    """Enable trace emission after the host has installed a safe sink."""
    global _ENABLED
    _ENABLED = True


def set_trace_context(**values: object):
    """Attach bounded correlation fields to trace records in this task."""
    current = dict(_CONTEXT.get())
    current.update({key: value for key, value in values.items() if value is not None})
    return _CONTEXT.set(current)


def reset_trace_context(token: object) -> None:
    _CONTEXT.reset(token)  # type: ignore[arg-type]


def trace_event(stage: str, event: str, **details: object) -> None:
    """Emit one JSON trace record; logging configuration controls persistence."""
    if not _ENABLED or not _LOGGER.isEnabledFor(logging.INFO):
        return
    payload = {
        "timestamp": time.time(),
        **_CONTEXT.get(),
        "stage": stage,
        "event": event,
    }
    payload.update({key: _safe_value(value) for key, value in details.items()})
    _LOGGER.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


@contextmanager
def trace_span(stage: str, **details: object) -> Iterator[None]:
    started = time.perf_counter()
    trace_event(stage, "started", **details)
    try:
        yield
    except BaseException as error:
        trace_event(
            stage,
            "failed",
            duration_ms=_duration_ms(started),
            error_type=type(error).__name__,
        )
        raise
    else:
        trace_event(stage, "completed", duration_ms=_duration_ms(started))


def _duration_ms(started: float) -> int:
    return min(86_400_000, max(0, int((time.perf_counter() - started) * 1_000)))


def _safe_value(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return value[:160]
        return value
    if isinstance(value, Mapping):
        return {str(key)[:40]: _safe_value(item) for key, item in list(value.items())[:16]}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_safe_value(item) for item in list(value)[:16]]
    return type(value).__name__
