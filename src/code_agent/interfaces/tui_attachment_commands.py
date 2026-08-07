from __future__ import annotations

from .attachment_input import attachment_path_arguments
from .terminal_display import DisplayKind


async def handle_attachment_command(app: object, instruction: str | None) -> bool:
    draft = getattr(app, "attachment_draft", None)
    if draft is None:
        app._append(DisplayKind.ERROR, "attachment input is unavailable")
        return False
    action, value = _instruction_parts(instruction)
    try:
        if action in {"", "list", "列表"}:
            _show(app, draft)
        elif action in {"clipboard", "剪贴板"}:
            added = await draft.add_clipboard_items()
            app._append(
                DisplayKind.METADATA,
                f"clipboard images staged · {len(added)} · total {len(draft.items)}",
            )
        elif action in {"clear", "清空"}:
            draft.clear()
            app._append(DisplayKind.METADATA, "attachment draft cleared")
        elif action in {"remove", "移除"}:
            removed = draft.remove(value)
            app._append(DisplayKind.METADATA, f"attachment removed · {removed.summary()}")
        else:
            paths = attachment_path_arguments(
                value if action in {"add", "添加"} else instruction or ""
            )
            added = await draft.add_paths(paths)
            app._append(
                DisplayKind.METADATA,
                f"attachments staged · {len(added)} · total {len(draft.items)}",
            )
    except (RuntimeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app.redraw()
    return True


def _show(app: object, draft: object) -> None:
    for row in draft.rows():
        app._append(DisplayKind.METADATA, row)


def _instruction_parts(instruction: str | None) -> tuple[str, str]:
    compact = (instruction or "").strip()
    if not compact:
        return "", ""
    parts = compact.split(maxsplit=1)
    return parts[0].casefold(), parts[1] if len(parts) == 2 else ""
