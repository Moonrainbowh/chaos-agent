from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from typing import Optional, Protocol

from code_agent.core.cancellation import CancellationToken

from .controller import AgentController
from .terminal_state import ApprovalBroker, ApprovalRequest, TerminalState


class SessionBrowser(Protocol):
    async def list_threads(self, *, limit: int = 100) -> Sequence[object]: ...


class WindowsTerminalApp:
    """A native ANSI TUI for Windows Terminal without UI package dependencies."""

    def __init__(
        self,
        controller: AgentController,
        approvals: ApprovalBroker,
        *,
        sessions: Optional[SessionBrowser] = None,
        write: Optional[Callable[[str], object]] = None,
    ) -> None:
        if not isinstance(controller, AgentController):
            raise TypeError("controller must be an AgentController")
        if not isinstance(approvals, ApprovalBroker):
            raise TypeError("approvals must be an ApprovalBroker")
        self.controller = controller
        self.approvals = approvals
        self.sessions = sessions
        self._write = write or _stdout_write
        self.state = TerminalState()
        self.input_text = ""
        self.current_thread_id: Optional[str] = None
        self.show_diff = False
        self.running = False
        self._run_task: Optional[asyncio.Task[None]] = None
        self._token: Optional[CancellationToken] = None
        self._approval_task: Optional[asyncio.Task[None]] = None
        self._pending_approval: Optional[ApprovalRequest] = None
        self._approval_done = asyncio.Event()
        self._session_choices: tuple[object, ...] = ()

    async def run(self, *, thread_id: Optional[str] = None) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsTerminalApp requires Windows")
        self.current_thread_id = thread_id
        self.running = True
        self._write("\x1b[?1049h\x1b[?25l")
        self._approval_task = asyncio.create_task(self._listen_approvals())
        try:
            while self.running:
                self.redraw()
                await self.handle_key(await asyncio.to_thread(_read_key))
        finally:
            await self._close_tasks()
            self._write("\x1b[?25h\x1b[?1049l")

    async def submit(self, text: str) -> bool:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not text.strip() or (self._run_task is not None and not self._run_task.done()):
            return False
        self.input_text = ""
        self.state.transcript.append("user: " + _safe_text(text))
        self._token = CancellationToken()
        self._run_task = asyncio.create_task(self._consume(text, self._token))
        self.redraw()
        return True

    async def wait_idle(self) -> None:
        if self._run_task is not None:
            await self._run_task

    async def handle_key(self, key: str) -> None:
        if self._pending_approval is not None and key.casefold() in {"y", "n"}:
            request = self._pending_approval
            self.approvals.resolve(request.request_id, key.casefold() == "y")
            self._pending_approval = None
            self._approval_done.set()
        elif key in {"\x03", "q"} and not self.input_text:
            self.running = False
            if self._token is not None:
                self._token.cancel("TUI closed")
        elif key == "\r":
            text = self.input_text
            self.input_text = ""
            await self.submit(text)
        elif key in {"\x08", "\x7f"}:
            self.input_text = self.input_text[:-1]
        elif key.casefold() == "d" and not self.input_text:
            self.show_diff = not self.show_diff
        elif key.casefold() == "s" and not self.input_text:
            await self._load_sessions()
        elif key.isdigit() and self._session_choices and not self.input_text:
            self._select_session(int(key) - 1)
        elif key.isprintable():
            self.input_text += key
        self.redraw()

    def redraw(self) -> None:
        columns, rows = shutil.get_terminal_size((100, 30))
        self._write(
            render_terminal(
                self.state,
                self.input_text,
                columns,
                rows,
                show_diff=self.show_diff,
                pending_approval=self._pending_approval,
                sessions=self._session_choices,
            )
        )

    async def _consume(self, text: str, token: CancellationToken) -> None:
        try:
            async for event in self.controller.ask(
                text,
                thread_id=self.current_thread_id,
                cancellation=token,
            ):
                self.state.apply(event)
                if self.state.thread_id is not None:
                    self.current_thread_id = self.state.thread_id
                self.redraw()
        except Exception as error:
            self.state.status = "error"
            self.state.transcript.append("error: " + type(error).__name__)
            self.redraw()

    async def _listen_approvals(self) -> None:
        while True:
            request = await self.approvals.next_request()
            self._pending_approval = request
            self._approval_done.clear()
            self.redraw()
            await self._approval_done.wait()

    async def _load_sessions(self) -> None:
        if self.sessions is None:
            self.state.status = "session list unavailable"
            return
        try:
            self._session_choices = tuple(await self.sessions.list_threads(limit=9))
            self.state.status = "select session 1-9"
        except Exception:
            self.state.status = "session list failed"

    def _select_session(self, index: int) -> None:
        if index < 0 or index >= len(self._session_choices):
            return
        identifier = getattr(self._session_choices[index], "id", None)
        if not isinstance(identifier, str):
            return
        self.current_thread_id = identifier
        self.state = TerminalState()
        self.state.thread_id = identifier
        self.state.status = "resumed"
        self._session_choices = ()

    async def _close_tasks(self) -> None:
        if self._token is not None:
            self._token.cancel("TUI closed")
        for task in (self._run_task, self._approval_task):
            if task is not None:
                task.cancel()
        tasks = [task for task in (self._run_task, self._approval_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def render_terminal(
    state: TerminalState,
    input_text: str,
    columns: int,
    rows: int,
    *,
    show_diff: bool = False,
    pending_approval: Optional[ApprovalRequest] = None,
    sessions: Sequence[object] = (),
) -> str:
    """Render one complete ANSI screen without trusting model terminal escapes."""
    width = max(40, columns)
    height = max(12, rows)
    thread = state.thread_id or "new"
    lines = [
        _clip("code-agent | Windows Terminal | session: " + thread, width),
        _clip("status: " + state.status + " | [s] sessions [d] diff [q] quit", width),
        "=" * width,
    ]
    if pending_approval is not None:
        lines.extend(
            (
                _clip("APPROVAL: " + pending_approval.name, width),
                _clip("Press Y to approve or N to deny.", width),
                "-" * width,
            )
        )
    if sessions:
        lines.append(_clip("Sessions: " + _session_line(sessions), width))
    diff_lines = _display_lines(state.diff)[:4] if show_diff and state.diff else []
    transcript_capacity = max(2, height - len(lines) - len(diff_lines) - 7)
    lines.extend(_display_lines("\n".join(state.transcript))[-transcript_capacity:])
    lines.append("-" * width)
    lines.extend(_display_lines("timeline: " + " | ".join(state.timeline[-3:]))[:2])
    if diff_lines:
        lines.append("diff:")
        lines.extend(diff_lines)
    lines.append("-" * width)
    lines.append(_clip("> " + _safe_text(input_text), width))
    return "\x1b[2J\x1b[H" + "\n".join(_clip(line, width) for line in lines)


def _read_key() -> str:
    import msvcrt

    key = msvcrt.getwch()
    if key in {"\x00", "\xe0"}:
        msvcrt.getwch()
        return ""
    return key


def _stdout_write(value: str) -> None:
    sys.stdout.write(value)
    sys.stdout.flush()


def _safe_text(value: str) -> str:
    return "".join(
        character if character >= " " or character in {"\n", "\t"} else "?"
        for character in value
    ).replace("\x1b", "?")


def _display_lines(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return _safe_text(value).replace("\t", "    ").splitlines() or [""]


def _clip(value: str, width: int) -> str:
    return value[:width]


def _session_line(sessions: Sequence[object]) -> str:
    labels = []
    for index, session in enumerate(sessions, start=1):
        identifier = getattr(session, "id", "?")
        preview = getattr(session, "last_message_preview", "") or ""
        labels.append(f"{index}:{identifier} {preview}")
    return " | ".join(labels)
