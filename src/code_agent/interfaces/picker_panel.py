from __future__ import annotations

from .terminal_display import clip_display, display_width, safe_text


def render_picker_panel(state: object, width: int) -> tuple[str, ...]:
    """Render a command picker as a compact bordered two-column overlay."""
    panel_width = max(5, width - 2)
    inner_width = max(1, panel_width - 4)
    title = " COMMANDS · Tab complete · ↑↓ Select "
    top = "╭─" + clip_display(title, inner_width)
    top += "─" * max(0, panel_width - display_width(top) - 1) + "╮"
    query = clip_display("⌕ " + state.query, inner_width)
    rows = [top, _panel_row(query, inner_width)]
    visible = state.visible
    label_width = min(
        max((display_width(item.label) for item in visible), default=0),
        max(1, inner_width // 2),
    )
    selected = state.selected
    for item in visible:
        marker = "›" if item == selected else " "
        label = clip_display(item.label, label_width)
        padding = " " * max(1, label_width - display_width(label) + 2)
        detail = item.detail if item.enabled else item.disabled_reason or "disabled"
        rows.append(
            _panel_row(
                clip_display(f"{marker} {label}{padding}{detail}", inner_width),
                inner_width,
            )
        )
    if len(state.matches) > len(visible):
        rows.append(
            _panel_row(f"  {state.selected_index + 1}/{len(state.matches)} matches", inner_width)
        )
    if state.error:
        rows.append(_panel_row("! " + safe_text(state.error), inner_width))
    rows.append("╰" + "─" * (panel_width - 2) + "╯")
    return tuple(rows)


def _panel_row(value: str, width: int) -> str:
    clipped = clip_display(safe_text(value).replace("\n", " "), width)
    return "│ " + clipped + " " * max(0, width - display_width(clipped)) + " │"
