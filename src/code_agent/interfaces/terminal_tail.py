from __future__ import annotations

from typing import Iterable
from .terminal_style import ColorMode
from .terminal_theme import design_for
from .terminal_tail_geometry import (
    LiveTailFrame, LiveTailGeometry, clear_live_tail, _compact_frame,
    _layout_input, _visible_input_rows, _rewrite_tail, _wrap_plain,
)
from .terminal_tail_content import _render_draft
from .terminal_tail_legacy import _normal_frame


def render_live_tail(
    input_text: str,
    status: str,
    width: int,
    *,
    cursor_index: int | None = None,
    assistant_draft: str = "",
    terminal_height: int = 30,
    color: ColorMode = ColorMode.AUTO,
    palette: Iterable[str] = (),
    status_icon: str = ".",
    status_color: str | None = None,
    status_context: str = "",
    theme: object = "symbol",
    motion_progress: float = 1.0,
    exiting: bool = False,
    active: bool = False,
) -> str:
    """Render a fresh bordered composer and status line without touching scrollback."""
    return render_live_tail_frame(
        input_text,
        status,
        width,
        cursor_index=cursor_index,
        assistant_draft=assistant_draft,
        terminal_height=terminal_height,
        color=color,
        palette=palette,
        status_icon=status_icon,
        status_color=status_color,
        status_context=status_context,
        theme=theme, motion_progress=motion_progress, exiting=exiting, active=active,
    ).text


def render_live_tail_frame(
    input_text: str,
    status: str,
    width: int,
    *,
    cursor_index: int | None = None,
    assistant_draft: str = "",
    terminal_height: int = 30,
    color: ColorMode = ColorMode.AUTO,
    palette: Iterable[str] = (),
    status_icon: str = ".",
    status_color: str | None = None,
    status_context: str = "",
    previous: LiveTailGeometry | None = None,
    theme: object = "symbol",
    motion_progress: float = 1.0,
    exiting: bool = False,
    active: bool = False,
) -> LiveTailFrame:
    """Rewrite only the previous dynamic tail and return its new cursor geometry."""
    safe_width = max(1, width)
    safe_height = max(1, terminal_height)
    if safe_width < 7 or safe_height < 4:
        return _compact_frame(
            input_text, status, safe_width, safe_height, cursor_index, previous
        )
    if design_for(theme) is not None:
        from .terminal_composer import render_designed_frame
        return render_designed_frame(
            input_text, status, safe_width, safe_height, cursor_index,
            assistant_draft, color, tuple(palette), status_icon, status_color,
            status_context, previous, theme, motion_progress, exiting, active,
        )
    return _normal_frame(
        input_text, status, safe_width, safe_height, cursor_index,
        assistant_draft, color, tuple(palette), status_icon, status_color,
        status_context, previous, theme=theme,
    )
