"""Single-theme appearance controls; no runtime side effects."""
from .terminal_display import DisplayKind


def set_theme(app: object, instruction: str | None) -> bool:
    value = (instruction or "").strip().casefold()
    if value in {"motion on", "motion off"}:
        app.motion.enabled = value.endswith(" on")
        app._append(DisplayKind.METADATA, "Motion " + ("on" if app.motion.enabled else "off"))
        app.redraw()
        return True
    if not value or value == "slate":
        app._append(DisplayKind.METADATA, "Muted Slate · fixed appearance. :theme motion off|on")
        return True
    app._append(DisplayKind.ERROR, "Theme switching removed. Use :theme motion off|on")
    return False
