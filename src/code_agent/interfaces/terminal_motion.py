"""Short, deterministic transitions restricted to the mutable terminal tail."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass

from .terminal_style import ColorMode, color_enabled
from .terminal_theme import design_for


@dataclass
class TailMotion:
    enabled: bool = True
    key: object = None
    started_at: float = 0.0
    duration: float = 0.28
    exiting: bool = False

    def observe(self, key: object, now: float) -> None:
        if key != self.key and not self.exiting:
            self.key, self.started_at = key, now

    def progress(self, now: float) -> float:
        if not self.enabled:
            return 1.0
        fraction = min(1.0, max(0.0, (now - self.started_at) / self.duration))
        return 1.0 - (1.0 - fraction) ** 3

    def active(self, now: float) -> bool:
        return self.enabled and now < self.started_at + self.duration


def motion_allowed(app: object) -> bool:
    return bool(
        app.motion.enabled and design_for(app.theme)
        and color_enabled(app.color) and app.color is not ColorMode.NEVER
        and "NO_COLOR" not in os.environ
        and os.environ.get("CHAOS_REDUCED_MOTION", "").lower() not in {"1", "true", "on"}
    )


async def watch_visuals(app: object) -> None:
    """Refresh transitions and idle resize; unchanged idle frames do not write."""
    was_active = False
    while app.running and not app._closing:
        size = shutil.get_terminal_size((100, 30))
        active = motion_allowed(app) and app.motion.active(time.monotonic())
        if active or was_active or app._drawn_size != (size.columns, size.lines):
            app.redraw()
        was_active = active
        await asyncio.sleep(1 / 30 if active else 0.1)


async def exit_transition(app: object) -> None:
    """Run after durable cancellation, before clearing the tail; at most 160 ms."""
    if not app.running and motion_allowed(app) and app._tail_geometry is not None:
        app.motion.exiting = True
        app.motion.started_at = time.monotonic()
        app.motion.duration = 0.16
        while app.motion.active(time.monotonic()):
            app.redraw()
            await asyncio.sleep(1 / 30)
