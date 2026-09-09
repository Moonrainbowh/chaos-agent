"""Private asynchronous credential input, isolated from the conversation editor."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .terminal_display import safe_text


@dataclass
class AuthPrompt:
    prompt: str
    result: asyncio.Future = field(repr=False)
    characters: list[str] = field(default_factory=list, repr=False)
    cursor: int = 0
    error: str = ""

    def clear(self) -> None:
        self.characters.clear()
        self.cursor = 0

    def insert(self, text: str) -> None:
        # Secrets are single-line. Pasted CR/LF never become submit events.
        text = text.strip("\r\n")
        if any(not char.isprintable() for char in text):
            self.error = "Use a single-line value; control characters were rejected."
            return
        if len(("".join(self.characters) + text).encode("utf-8")) > 16384:
            self.error = "Authentication input exceeds 16 KiB; input unchanged."
            return
        self.characters[self.cursor:self.cursor] = text
        self.cursor += len(text)
        self.error = ""


def auth_active(app) -> bool:
    task = getattr(app, "_auth_task", None)
    return bool(getattr(app, "_auth_prompt", None) or (task is not None and not task.done()))


async def read_auth_input(app, prompt: str) -> str:
    """Wait for explicit Enter without using InputBuffer or transcript/history."""
    if getattr(app, "_auth_prompt", None) is not None:
        raise RuntimeError("Authentication input is already pending")
    if getattr(app, "_closing", False):
        raise asyncio.CancelledError
    state = AuthPrompt(safe_text(prompt).replace("\n", " ")[:300], asyncio.get_running_loop().create_future())
    app._auth_prompt = state
    app.composer_expanded = True
    app.redraw()
    try:
        return await state.result
    finally:
        state.clear()
        if getattr(app, "_auth_prompt", None) is state:
            app._auth_prompt = None
        if not getattr(app, "_closing", False):
            app.redraw()


async def cancel_auth_input(app) -> None:
    """Cancel login and release only private authentication buffers."""
    state = getattr(app, "_auth_prompt", None)
    if state is not None:
        state.clear()
        if not state.result.done():
            state.result.cancel()
        app._auth_prompt = None
    task = getattr(app, "_auth_task", None)
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def handle_auth_key(app, key: str) -> bool:
    """Consume every key while logging in, keeping ordinary task input untouched."""
    if not auth_active(app):
        return False
    if key in {"\x1b", "\x03"}:
        await cancel_auth_input(app)
        return True
    state = getattr(app, "_auth_prompt", None)
    if state is None or state.result.done():
        return True
    if key == "\r":
        value = "".join(state.characters).strip()
        if value:
            state.clear()
            state.result.set_result(value)
        else:
            state.error = "Enter a value or press Esc to cancel."
    elif key == "\x15":
        state.clear()
    elif key in {"\x08", "\x7f"} and state.cursor:
        state.cursor -= 1
        del state.characters[state.cursor]
    elif key == "delete" and state.cursor < len(state.characters):
        del state.characters[state.cursor]
    elif key in {"left", "right", "home", "end"}:
        move = {"left": state.cursor - 1, "right": state.cursor + 1, "home": 0, "end": len(state.characters)}
        state.cursor = max(0, min(len(state.characters), move[key]))
    elif key.startswith("\x1b[200~") and key.endswith("\x1b[201~"):
        state.insert(key[6:-6])
    elif key not in {"up", "down", "delete", "alt+v", "shift+enter", "page_up", "page_down"} and key.isprintable():
        state.insert(key)
    return True


def auth_input_view(app):
    """Return only masked text and local hints; never return private input."""
    if not auth_active(app):
        return None
    state = getattr(app, "_auth_prompt", None)
    if state is None:
        return "", 0, ("Authentication in progress · Esc cancels",)
    masked = "*" * min(64, len(state.characters))
    hints = (state.prompt, state.error or "Hidden input · Enter confirms · Esc cancels")
    return masked, min(64, state.cursor), hints
