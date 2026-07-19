from __future__ import annotations

from typing import Any


def available_services(app: Any) -> set[str]:
    return {
        name for name in ("sessions", "evidence", "tasks", "history", "profiles", "skills", "mcp", "rewind")
        if getattr(app, name, None) is not None
    }
