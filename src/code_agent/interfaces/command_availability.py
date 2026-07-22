from __future__ import annotations

from typing import Any


def available_services(app: Any) -> set[str]:
    return {
        name
        for name in (
            "sessions",
            "evidence",
            "checkpoints",
            "tasks",
            "history",
            "modes",
            "permissions",
            "workflows",
            "skills",
            "mcp",
            "plugins",
        )
        if getattr(app, name, None) is not None
    }
