from __future__ import annotations
import asyncio
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from typing import Optional, Protocol
from code_agent.core.cancellation import CancellationToken
from .controller import AgentController
from .history import ThreadHistoryReader, load_thread_history
from .terminal_state import ApprovalBroker, ApprovalRequest, TerminalState
from .task_controller import ForegroundTaskController
from .tui_commands import TuiCommandKind, parse_tui_command
from .i18n import EN_US, ZH_CN, UiCatalog
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
        tasks: ForegroundTaskController | None = None,
        history: Optional[ThreadHistoryReader] = None,
        write: Optional[Callable[[str], object]] = None,
    ) -> None:
        if not isinstance(controller, AgentController):
            raise TypeError("controller must be an AgentController")
        if not isinstance(approvals, ApprovalBroker):
            raise TypeError("approvals must be an ApprovalBroker")
        self.controller = controller
        self.approvals = approvals
        self.sessions = sessions
        self.tasks = tasks
        self.active_task_id: str | None = None
        self.catalog = ZH_CN
        self.history = history
        self._write = write or _stdout_write
        self.state = TerminalState()
        self.input_text = ""
        self.current_thread_id: Optional[str] = None
        self.history_offset = 0
        self.show_diff = False
        self.show_reasoning = False
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
        if thread_id is not None:
            await self.restore_thread(thread_id)
        self.running = True
        self._write("\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h")
        self._approval_task = asyncio.create_task(self._listen_approvals())
        try:
            while self.running:
                self.redraw()
                await self.handle_key(await asyncio.to_thread(_read_key))
        finally:
            await self._close_tasks()
            self._write("\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l")
    async def submit(self, text: str) -> bool:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not text.strip():
            return False
        command = parse_tui_command(text)
        if command is not None:
            return await self._handle_task_command(command)
        if self._run_task is not None and not self._run_task.done():
            if self.tasks is not None and self.active_task_id is not None:
                await self.tasks.steer(self.active_task_id, text)
                self.state.status = "steering queued"
                return True
            return False
        self.input_text = ""
        self.history_offset = 0
        self.state.transcript.append("user: " + _safe_text(text))
        self._token = CancellationToken()
        if self.tasks is not None:
            record = await self.tasks.start(text)
            self.active_task_id = record.id
            self._run_task = asyncio.create_task(self._consume_task(record.id, text))
        else:
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
        elif key == "\x1b" and self.active_task_id and self.tasks is not None:
            await self.tasks.pause(self.active_task_id)
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
        elif key.casefold() == "r" and not self.input_text:
            self.show_reasoning = not self.show_reasoning
        elif key.casefold() == "s" and not self.input_text:
            await self._load_sessions()
        elif key.isdigit() and self._session_choices and not self.input_text:
            await self._select_session(int(key) - 1)
        elif (
            key in {"page_up", "page_down", "home", "end", "mouse_scroll_up", "mouse_scroll_down"}
            and self._pending_approval is None
            and not self.input_text
        ):
            self._scroll_history(key)
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
                show_reasoning=self.show_reasoning,
                pending_approval=self._pending_approval,
                sessions=self._session_choices,
                history_offset=self.history_offset,
            )
        )
    async def restore_thread(self, thread_id: str) -> bool:
        """Restore a persisted session without discarding the displayed state on error."""
        if self.history is None:
            self.state.status = "session history unavailable"
            return False
        try:
            history = await load_thread_history(self.history, thread_id)
        except Exception:
            self.state.status = "session restore failed"
            return False
        restored = TerminalState()
        restored.restore(history)
        self.state = restored
        self.current_thread_id = thread_id
        self.history_offset = 0
        return True
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
    async def _consume_task(self, task_id: str, text: str) -> None:
        try:
            if self.tasks is None:
                return
            async for event in self.tasks.events(task_id, text):
                self.state.apply(event)
                self.redraw()
        except Exception as error:
            self.state.status = "error"
            self.state.transcript.append("error: " + type(error).__name__)
            self.redraw()

    async def _handle_task_command(self, command: object) -> bool:
        if self.tasks is None:
            return False
        kind = getattr(command, "kind", None)
        task_id = getattr(command, "task_id", None) or self.active_task_id
        if kind is TuiCommandKind.TASKS:
            records = await self.tasks.list(include_terminal=True)
            self.state.transcript.append("tasks: " + " | ".join(f"{task.id}:{task.status.value}" for task in records))
        elif kind is TuiCommandKind.PAUSE and task_id:
            await self.tasks.pause(task_id)
        elif kind is TuiCommandKind.STOP and task_id:
            await self.tasks.stop(task_id)
        elif kind is TuiCommandKind.RESUME and task_id:
            self.active_task_id = task_id
            self._run_task = asyncio.create_task(self._consume_task(task_id, "continue safely"))
        elif kind is TuiCommandKind.STEER and task_id:
            await self.tasks.steer(task_id, getattr(command, "instruction", ""))
        elif kind is TuiCommandKind.LANGUAGE:
            self.catalog = ZH_CN if getattr(command, "instruction", "") in {"zh", "zh-CN"} else EN_US
        else:
            return False
        return True
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
    async def _select_session(self, index: int) -> None:
        if index < 0 or index >= len(self._session_choices):
            return
        identifier = getattr(self._session_choices[index], "id", None)
        if not isinstance(identifier, str) or not identifier.strip():
            return
        if await self.restore_thread(identifier):
            self._session_choices = ()
    def _scroll_history(self, key: str) -> None:
        _, rows = shutil.get_terminal_size((100, 30))
        capacity = _transcript_capacity(
            self.state,
            rows,
            show_diff=self.show_diff,
            show_reasoning=self.show_reasoning,
            pending_approval=self._pending_approval,
            sessions=self._session_choices,
        )
        max_offset = _history_max_offset(self.state.transcript, capacity)
        if key == "page_up":
            self.history_offset = min(max_offset, self.history_offset + 1)
        elif key == "mouse_scroll_up":
            self.history_offset = min(max_offset, self.history_offset + 3)
        elif key == "page_down":
            self.history_offset = max(0, self.history_offset - 1)
        elif key == "mouse_scroll_down":
            self.history_offset = max(0, self.history_offset - 3)
        elif key == "home":
            self.history_offset = max_offset
        elif key == "end":
            self.history_offset = 0
    async def _close_tasks(self) -> None:
        if self.tasks is not None and self.active_task_id is not None and self._run_task is not None and not self._run_task.done():
            await self.tasks.pause(self.active_task_id, "TUI closed")
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
    show_reasoning: bool = False,
    pending_approval: Optional[ApprovalRequest] = None,
    sessions: Sequence[object] = (),
    history_offset: int = 0,
    catalog: UiCatalog = EN_US,
) -> str:
    """Render one complete ANSI screen without trusting model terminal escapes."""
    width = max(40, columns)
    height = max(12, rows)
    thread = _safe_text(state.thread_id or "new")
    lines = [
        _clip("Chaos Agent | Windows Terminal | session: " + thread, width),
        _clip(_safe_text("status: " + state.status), width),
        _clip("[s] sessions [d] diff [r] reasoning [q] quit | history: wheel/PageUp/PageDown Home/End", width),
    ]
    if state.task_id and state.task_status not in {"completed", "failed"}:
        lines.insert(2, _clip(f"{catalog.task} {state.task_id} · {state.task_status} · Esc {catalog.paused} · /任务", width))
    lines.extend(_display_lines("\n".join(state.summary))[:3])
    if pending_approval is not None:
        lines.extend(
            (
                _clip(_safe_text("APPROVAL: " + pending_approval.name), width),
                _clip("Press Y to approve or N to deny.", width),
                "-" * width,
            )
        )
    if sessions:
        lines.append(_clip(_safe_text("Sessions: " + _session_line(sessions)), width))
    reasoning_lines = _reasoning_lines(state, show_reasoning)
    lines.extend(_clip(line, width) for line in reasoning_lines)
    diff_lines = _display_lines(state.diff)[:4] if show_diff and state.diff else []
    lines.append(_clip(_safe_text("recent: " + " | ".join(state.timeline[-4:])), width))
    lines.append("-" * width)
    transcript_capacity = _transcript_capacity(
        state,
        height,
        show_diff=show_diff,
        show_reasoning=show_reasoning,
        pending_approval=pending_approval,
        sessions=sessions,
    )
    lines.extend(_history_window(state.transcript, transcript_capacity, history_offset))
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
        return {"I": "page_up", "Q": "page_down", "G": "home", "O": "end"}.get(
            msvcrt.getwch(), ""
        )
    if key == "\x1b" and msvcrt.kbhit():
        sequence = key
        while msvcrt.kbhit():
            sequence += msvcrt.getwch()
        return _decode_ansi_input(sequence)
    return key


def _decode_ansi_input(sequence: str) -> str:
    if sequence.startswith("\x1b[<64;") and sequence.endswith("M"):
        return "mouse_scroll_up"
    if sequence.startswith("\x1b[<65;") and sequence.endswith("M"):
        return "mouse_scroll_down"
    return sequence
def _stdout_write(value: str) -> None:
    sys.stdout.write(value)
    sys.stdout.flush()
def _safe_text(value: str) -> str:
    return "".join(character if character >= " " or character in {"\n", "\t"} else "?" for character in value).replace("\x1b", "?")
def _display_lines(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return _safe_text(value).replace("\t", "    ").splitlines() or [""]
def _clip(value: str, width: int) -> str:
    return value[:width]
def _transcript_capacity(
    state: TerminalState,
    rows: int,
    *,
    show_diff: bool,
    show_reasoning: bool,
    pending_approval: Optional[ApprovalRequest],
    sessions: Sequence[object],
) -> int:
    summary_lines = len(_display_lines("\n".join(state.summary))[:3])
    diff_lines = len(_display_lines(state.diff)[:4]) if show_diff and state.diff else 0
    reasoning_lines = len(_reasoning_lines(state, show_reasoning))
    fixed_lines = 7 + summary_lines + (1 if state.task_id and state.task_status not in {"completed", "failed"} else 0) + (3 if pending_approval else 0) + bool(sessions) + reasoning_lines
    return max(0, max(12, rows) - fixed_lines - diff_lines - bool(diff_lines))


def _reasoning_lines(state: TerminalState, show_reasoning: bool) -> list[str]:
    if not state.reasoning:
        return []
    if not show_reasoning:
        return ["reasoning: hidden ([r] to expand)"]
    return ["reasoning:", *_display_lines("\n".join(state.reasoning))[:3]]
def _history_max_offset(transcript: Sequence[str], capacity: int) -> int:
    line_count = sum(len(_display_lines(entry)) for entry in transcript)
    return max(0, line_count - capacity)
def _history_window(transcript: Sequence[str], capacity: int, history_offset: int) -> list[str]:
    """Return a fixed-size transcript slice, offset backward from the live edge."""
    if capacity <= 0:
        return []
    lines = [line for entry in transcript for line in _display_lines(entry)]
    max_offset = _history_max_offset(transcript, capacity)
    offset = min(max(0, history_offset), max_offset)
    end = len(lines) - offset
    return lines[max(0, end - capacity):end]
def _session_line(sessions: Sequence[object]) -> str:
    labels = []
    for index, session in enumerate(sessions, start=1):
        identifier = getattr(session, "id", "?")
        preview = getattr(session, "last_message_preview", "") or ""
        labels.append(f"{index}:{identifier} {preview}")
    return " | ".join(labels)
