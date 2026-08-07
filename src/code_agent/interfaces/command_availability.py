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
            "attachments",
        )
        if (
            getattr(app, "attachment_draft", None) is not None
            if name == "attachments"
            else getattr(app, name, None) is not None
        )
    }
