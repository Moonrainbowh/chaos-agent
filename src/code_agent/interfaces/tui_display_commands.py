from __future__ import annotations

from typing import Any

from .i18n import Language, catalog_for
from .terminal_display import DisplayKind
from .terminal_renderer import ColorMode, Theme
from .tui_commands import TuiCommand, TuiCommandKind


def _language(app: Any, command: TuiCommand) -> bool:
    value = (command.instruction or "").casefold()
    if value in {"zh", "zh-cn"}:
        app.catalog = catalog_for(Language.ZH_CN)
    elif value in {"en", "en-us"}:
        app.catalog = catalog_for(Language.EN_US)
    else:
        app._append(DisplayKind.ERROR, "language must be zh-CN or en")
        return False
    app._append(
        DisplayKind.METADATA,
        "语言已切换" if app.catalog.language is Language.ZH_CN else "language updated",
    )
    return True


def _theme(app: Any, command: TuiCommand) -> bool:
    try:
        app.theme = Theme(command.instruction or "")
    except ValueError:
        app._append(DisplayKind.ERROR, "theme must be signal, symbol, or plain")
        return False
    app._append(DisplayKind.METADATA, "theme updated")
    return True


def _color(app: Any, command: TuiCommand) -> bool:
    try:
        app.color = ColorMode(command.instruction or "")
    except ValueError:
        app._append(DisplayKind.ERROR, "color must be auto, always, or never")
        return False
    app._append(DisplayKind.METADATA, "color updated")
    return True


def _glyphs(app: Any, command: TuiCommand) -> bool:
    glyphs = command.instruction
    if glyphs == "ascii":
        app.theme = Theme.SIGNAL
    elif glyphs == "unicode":
        app.theme = Theme.SYMBOL
    else:
        app._append(DisplayKind.ERROR, "glyphs must be ascii or unicode")
        return False
    app._append(DisplayKind.METADATA, "glyphs updated")
    return True


async def handle_display_command(app: Any, command: TuiCommand) -> bool | None:
    handlers = {
        TuiCommandKind.LANGUAGE: _language,
        TuiCommandKind.THEME: _theme,
        TuiCommandKind.COLOR: _color,
        TuiCommandKind.GLYPHS: _glyphs,
    }
    handler = handlers.get(command.kind)
    return None if handler is None else handler(app, command)
