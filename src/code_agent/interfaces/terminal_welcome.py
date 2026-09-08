"""A compact startup identity with capability facts available at :status."""
from __future__ import annotations

from .terminal_display import DisplayKind, safe_text, text_entry
from .terminal_renderer import render_entries
from .terminal_style import BRIGHT_CYAN, ColorMode, colorize
from .terminal_theme import design_for, recolor


def render_welcome(project: str, capability: object, theme: object, width: int, color: ColorMode) -> str:
    """Render only facts supplied by the host; do not infer permission or readiness."""
    from .terminal_transcript_markdown import _wrap_display
    design = design_for(theme)
    title = "CHAOS AGENT" + ("  /  MUTED SLATE" if design else "")
    title_lines = _wrap_display(title, max(1, width))
    title_rendered = "\n".join(colorize(line, BRIGHT_CYAN, color) for line in title_lines)
    permission = capability.permission
    runtime = (capability.runtime_summary or "runtime details unavailable").split(" via ")[0]
    details = (
        f"{safe_text(project)} · {capability.mode.model} · {capability.mode.effective_reasoning_effort}",
        f"permission: {permission.approval_mode.value} · write {'yes' if permission.allow_workspace_write else 'no'} · network {'yes' if permission.allow_network else 'no'}",
        f"runtime: {runtime} · :status for full workspace/path details",
        ": for commands · :theme motion off to reduce animations",
    )
    entries = (text_entry(DisplayKind.METADATA, line) for line in details)
    rendered = render_entries(entries, width, theme=theme, color=color)
    return "\n" + recolor(title_rendered, theme) + "\n" + rendered + "\n\n\r"
