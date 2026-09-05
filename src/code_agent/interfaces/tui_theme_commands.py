"""Appearance preferences only affect the local UI, never runtime permissions."""
from __future__ import annotations

from .terminal_display import DisplayKind
from .terminal_style import ColorMode
from .terminal_theme import Theme, DESIGNS


def set_theme(app: object, instruction: str | None) -> bool:
    value = (instruction or "").strip().casefold()
    if not value:
        choices = "\n".join(f"  :theme {theme.value:<7}  {item.description}" for theme, item in DESIGNS.items())
        app._append(DisplayKind.METADATA, f"Appearance · {app.theme.value}\n{choices}\n  :theme motion off|on · reduce animations\n  :theme plain · legacy / no color")
        return True
    if value in {"motion on", "motion off"}:
        app.motion.enabled = value.endswith(" on")
        app._append(DisplayKind.METADATA, "Motion " + ("on" if app.motion.enabled else "off"))
        return True
    try:
        selected = Theme(value)
    except ValueError:
        app._append(DisplayKind.ERROR, "Use :theme aurora, ember, mono, or :theme motion off|on")
        return False
    app.theme = selected
    if selected is Theme.PLAIN:
        app.color = ColorMode.NEVER
    elif app.color is ColorMode.NEVER:
        app.color = ColorMode.AUTO
    app._append(DisplayKind.METADATA, f"Appearance · {selected.value}")
    app.redraw()
    return True
