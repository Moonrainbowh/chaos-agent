from __future__ import annotations

from .terminal_display import clip_display, display_width
from .terminal_markdown import render_streaming_markdown_rows
from .terminal_style import BORDER_GRAY, BRAND_CYAN, DIM_GRAY, BODY_WHITE, ColorMode, colorize


def _style_box_border(value: str, color: ColorMode, *, border_code: str = BORDER_GRAY) -> str:
    return colorize(value, border_code, color)


def _style_box_row(
    prompt: str,
    value: str,
    padding: str,
    color: ColorMode,
    *,
    placeholder: bool,
    border_code: str = BORDER_GRAY,
    prompt_code: str = BRAND_CYAN,
) -> str:
    plain = "│ " + prompt + value + padding + " │"
    body_code = DIM_GRAY if placeholder else BODY_WHITE
    return (
        colorize("│ ", border_code, color)
        + colorize(prompt, prompt_code, color)
        + colorize(value + padding, body_code, color)
        + colorize(" │", border_code, color)
    )


def _render_palette(items: tuple[str, ...], width: int, color: ColorMode) -> list[str]:
    selected = next((index for index, item in enumerate(items) if "› " in item[:5]), -1)
    return [colorize(clip_display("  " + item, width), BRAND_CYAN if index == selected else DIM_GRAY, color) for index, item in enumerate(items)]


def _render_draft(value: str, width: int, max_rows: int, color: ColorMode, *, modern: bool = False) -> list[str]:
    if not value or max_rows <= 0:
        return []
    star = "✦" if modern else "◆"
    title = clip_display(f"{star} Responding", width)
    prefix = "  " if width > 2 else ""
    content_width = max(1, width - display_width(prefix))
    from .terminal_ac_layout import render_ac_rows
    render = (lambda value, width, color: render_ac_rows(value, width, color, incomplete=True)) if modern else render_streaming_markdown_rows
    rows = render(value, content_width, color)
    if len(rows) > max_rows and content_width > 2:
        rows = render(value, content_width - 2, color)
    clipped = rows[-max_rows:]
    if len(rows) > len(clipped):
        clipped[0] = colorize("… ", DIM_GRAY, color) + clipped[0]
    return [colorize(title, BRAND_CYAN, color)] + [
        colorize(prefix, BODY_WHITE, color) + row for row in clipped
    ]


