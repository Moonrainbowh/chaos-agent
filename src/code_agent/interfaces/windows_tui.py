from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from typing import Optional, Protocol

from code_agent.core.cancellation import CancellationToken
from code_agent.core.events import EventKind
from .controller import AgentController
from .command_palette import filter_palette
from .history import ThreadHistoryReader, load_thread_history
from .input_buffer import InputBuffer
from .terminal_display import DisplayKind, text_entry
from .terminal_renderer import ColorMode, Theme, render_entries, render_live_tail
from .terminal_state import ApprovalBroker, ApprovalRequest, TerminalState
from .profile_control import ProfileControl
from code_agent.skills.registry import SkillActivation
from code_agent.mcp.registry import McpRegistry
from .task_controller import ForegroundTaskController
from .tui_commands import ParseOutcome, TuiCommandKind, parse_tui_command


class SessionBrowser(Protocol):
    async def list_threads(self, *, limit: int = 100) -> Sequence[object]: ...


class WindowsTerminalApp:
    """Append-only Windows Terminal interaction without alternate-screen control."""

    def __init__(self, controller: AgentController, approvals: ApprovalBroker, *, sessions: Optional[SessionBrowser] = None, tasks: ForegroundTaskController | None = None, history: Optional[ThreadHistoryReader] = None, profiles: ProfileControl | None = None, skills: SkillActivation | None = None, mcp: McpRegistry | None = None, write: Optional[Callable[[str], object]] = None) -> None:
        self.controller, self.approvals = controller, approvals
        self.sessions, self.tasks, self.history, self.profiles, self.skills, self.mcp, self._write = sessions, tasks, history, profiles, skills, mcp, write or _stdout_write
        self.state = TerminalState(); self.input = InputBuffer(); self.current_thread_id: str | None = None
        self.active_task_id: str | None = None; self.running = False; self._run_task: asyncio.Task[None] | None = None
        self._animation_task: asyncio.Task[None] | None = None; self._spinner_index = 0
        self._token: CancellationToken | None = None; self._approval_task: asyncio.Task[None] | None = None
        self._pending_approval: ApprovalRequest | None = None; self._approval_done = asyncio.Event()
        self._flushed_entries = 0
        self.theme, self.color = Theme.SIGNAL, ColorMode.AUTO

    async def run(self, *, thread_id: str | None = None) -> None:
        if os.name != "nt": raise RuntimeError("WindowsTerminalApp requires Windows")
        if thread_id: await self.restore_thread(thread_id)
        self.running = True; self._approval_task = asyncio.create_task(self._listen_approvals()); self.redraw()
        try:
            while self.running: await self.handle_key(await asyncio.to_thread(_read_key))
        finally: await self._close_tasks()

    async def submit(self, text: str) -> bool:
        if not isinstance(text, str): raise TypeError("text must be a string")
        if not text.strip(): return False
        parsed = parse_tui_command(text)
        if parsed.is_command: return await self._handle_command(parsed)
        if parsed.error: self._append(DisplayKind.ERROR, parsed.error); return False
        self._append(DisplayKind.USER, text); self.state.begin_run(); self._token = CancellationToken()
        if self.tasks:
            record = await self.tasks.start(text); self.active_task_id = record.id
            self._run_task = asyncio.create_task(self._consume_task(record.id, text))
        else: self._run_task = asyncio.create_task(self._consume(text, self._token))
        self._start_animation()
        self.redraw(); return True

    async def wait_idle(self) -> None:
        if self._run_task: await self._run_task
        await self._stop_animation()

    async def handle_key(self, key: str) -> None:
        if self._pending_approval and key.casefold() in {"y", "n"}:
            self.approvals.resolve(self._pending_approval.request_id, key.casefold() == "y"); self._pending_approval = None; self._approval_done.set()
        elif key == "\x03": self.running = False; self._token.cancel("TUI closed") if self._token else None
        elif key == "\x15": self.input.clear()
        elif key == "\r": await self.submit(self.input.submit())
        elif key == "left": self.input.move_left()
        elif key == "right": self.input.move_right()
        elif key == "home": self.input.move_home()
        elif key == "end": self.input.move_end()
        elif key == "up": self.input.previous()
        elif key == "down": self.input.next()
        elif key in {"\x08", "\x7f"}: self.input.backspace()
        elif key == "delete": self.input.delete()
        elif key.isprintable(): self.input.insert(key)
        self.redraw()

    def redraw(self) -> None:
        palette = (item.display for item in filter_palette(self.input.text))
        status, icon, status_color = self._status_presentation()
        self._write(render_live_tail(self.input.text, status, self._columns(), cursor_index=self.input.cursor, color=self.color, palette=palette, status_icon=icon, status_color=status_color))

    async def restore_thread(self, thread_id: str) -> bool:
        if self.history is None: self._append(DisplayKind.ERROR, "session history unavailable"); return False
        try: history = await load_thread_history(self.history, thread_id)
        except Exception: self._append(DisplayKind.ERROR, "session restore failed"); return False
        restored = TerminalState(); restored.restore(history); self.state = restored; self.current_thread_id = thread_id
        self._write(render_entries(restored.entries, 100, theme=self.theme, color=self.color) + "\n"); self._flushed_entries = len(restored.entries); return True

    async def _consume(self, text: str, token: CancellationToken) -> None:
        try:
            async for event in self.controller.ask(text, thread_id=self.current_thread_id, cancellation=token):
                self.state.apply(event)
                if event.kind is not EventKind.MODEL_EVENT:
                    self._flush_pending_entries()
                if self.state.thread_id: self.current_thread_id = self.state.thread_id
                self.redraw()
        except Exception as error: self._append(DisplayKind.ERROR, type(error).__name__)

    async def _consume_task(self, task_id: str, text: str) -> None:
        if not self.tasks: return
        try:
            async for event in self.tasks.events(task_id, text):
                self.state.apply(event)
                if event.kind is not EventKind.MODEL_EVENT:
                    self._flush_pending_entries()
                self.redraw()
        except Exception as error: self._append(DisplayKind.ERROR, type(error).__name__)

    async def _handle_command(self, outcome: ParseOutcome) -> bool:
        command = outcome.command
        assert command is not None
        if command.kind is TuiCommandKind.DIFF: self._append(DisplayKind.METADATA, self.state.diff or "no diff available")
        elif command.kind is TuiCommandKind.STATUS: self._append(DisplayKind.METADATA, self._status_line())
        elif command.kind is TuiCommandKind.HELP: self._append(DisplayKind.METADATA, " ".join(item.display for item in filter_palette("/")))
        elif command.kind is TuiCommandKind.LANGUAGE: self._append(DisplayKind.METADATA, "language updated")
        elif command.kind is TuiCommandKind.THEME:
            try: self.theme = Theme(command.instruction or "")
            except ValueError: self._append(DisplayKind.ERROR, "theme must be signal, symbol, or plain"); return False
            self._append(DisplayKind.METADATA, "theme updated")
        elif command.kind is TuiCommandKind.COLOR:
            try: self.color = ColorMode(command.instruction or "")
            except ValueError: self._append(DisplayKind.ERROR, "color must be auto, always, or never"); return False
            self._append(DisplayKind.METADATA, "color updated")
        elif command.kind is TuiCommandKind.GLYPHS:
            glyphs = command.instruction
            if glyphs == "ascii": self.theme = Theme.SIGNAL
            elif glyphs == "unicode": self.theme = Theme.SYMBOL
            else: self._append(DisplayKind.ERROR, "glyphs must be ascii or unicode"); return False
            self._append(DisplayKind.METADATA, "glyphs updated")
        elif command.kind is TuiCommandKind.MODEL:
            if self.profiles is None: self._append(DisplayKind.ERROR, "model profiles are unavailable"); return False
            if command.instruction in {None, "列表", "list"}:
                self._append(DisplayKind.METADATA, " | ".join(f"{item.name}:{item.model}" for item in self.profiles.list()))
            elif command.instruction.startswith("使用 ") or command.instruction.startswith("use "):
                name = command.instruction.split(maxsplit=1)[1]
                try: selected = self.profiles.use(name, idle=self._run_task is None or self._run_task.done())
                except (ValueError, RuntimeError) as error: self._append(DisplayKind.ERROR, str(error)); return False
                self._append(DisplayKind.METADATA, f"model selected: {selected.name}:{selected.model}")
            else: self._append(DisplayKind.ERROR, "model expects list or use <profile>"); return False
        elif command.kind is TuiCommandKind.SKILLS:
            if self.skills is None: self._append(DisplayKind.ERROR, "skills are unavailable"); return False
            action, _, identifier = (command.instruction or "").partition(" ")
            if action in {"", "列表", "list"}: self._append(DisplayKind.METADATA, " | ".join(skill.identifier for skill in self.skills.available()))
            elif action in {"信息", "info"} and identifier: self._append(DisplayKind.METADATA, self.skills.info(identifier).description)
            elif action in {"启用", "enable"} and identifier: self.skills.activate(identifier, approved=True); self._append(DisplayKind.METADATA, f"skill enabled: {identifier}")
            elif action in {"禁用", "disable"} and identifier: self.skills.deactivate(identifier); self._append(DisplayKind.METADATA, f"skill disabled: {identifier}")
            else: self._append(DisplayKind.ERROR, "skills expects list, info, enable, or disable"); return False
        elif command.kind is TuiCommandKind.MCP:
            if self.mcp is None: self._append(DisplayKind.ERROR, "MCP is unavailable"); return False
            action, _, name = (command.instruction or "状态").partition(" ")
            if action not in {"状态", "status"}: self._append(DisplayKind.ERROR, "MCP supports status only"); return False
            try: servers = self.mcp.status(name or None)
            except KeyError: self._append(DisplayKind.ERROR, "unknown configured MCP server"); return False
            self._append(DisplayKind.METADATA, " | ".join(f"{server.name}:{'enabled' if server.enabled else 'disabled'}" for server in servers))
        elif self.tasks and command.kind is TuiCommandKind.TASKS:
            records = await self.tasks.list(include_terminal=True); self._append(DisplayKind.METADATA, " | ".join(f"{item.id}:{item.status.value}" for item in records))
        elif self.tasks and command.kind in {TuiCommandKind.PAUSE, TuiCommandKind.STOP, TuiCommandKind.RESUME, TuiCommandKind.STEER}:
            task_id = command.task_id or self.active_task_id
            if not task_id: self._append(DisplayKind.ERROR, "no active task"); return False
            if command.kind is TuiCommandKind.PAUSE: await self.tasks.pause(task_id)
            elif command.kind is TuiCommandKind.STOP: await self.tasks.stop(task_id)
            elif command.kind is TuiCommandKind.STEER: await self.tasks.steer(task_id, command.instruction or "")
            else: self._run_task = asyncio.create_task(self._consume_task(task_id, "continue safely"))
        else: self._append(DisplayKind.ERROR, "command is unavailable")
        return True

    async def _listen_approvals(self) -> None:
        while True: self._pending_approval = await self.approvals.next_request(); self._approval_done.clear(); self.redraw(); await self._approval_done.wait()

    def _append(self, kind: DisplayKind, value: object) -> None:
        self.state.entries.append(text_entry(kind, value)); self.state.transcript.append(self.state.entries[-1].text); self._flush_pending_entries()

    def _flush_pending_entries(self) -> None:
        new = self.state.entries[self._flushed_entries:]
        if new:
            self._write("\r\x1b[2K" + render_entries(new, self._columns(), theme=self.theme, color=self.color) + "\n\r\x1b[2K")
            self._flushed_entries = len(self.state.entries)

    def _columns(self) -> int:
        return shutil.get_terminal_size((100, 30)).columns

    def _status_presentation(self) -> tuple[str, str, str | None]:
        if self.state.status == "running":
            detail = self.state.active_action or "正在生成回复"
            return "处理中 · " + detail, "|/-\\"[self._spinner_index % 4], "38;5;250"
        if self.state.status == "completed":
            return self.state.execution_summary or "已完成", "+", "38;5;114"
        if self.state.status == "error": return "处理失败", "×", "31"
        if self.state.status == "cancelled": return "已取消", "!", "33"
        return "就绪", "·", None

    def _start_animation(self) -> None:
        if self._animation_task is None or self._animation_task.done():
            self._animation_task = asyncio.create_task(self._animate())

    async def _stop_animation(self) -> None:
        if self._animation_task and not self._animation_task.done():
            self._animation_task.cancel()
        if self._animation_task:
            await asyncio.gather(self._animation_task, return_exceptions=True)
        self._animation_task = None

    async def _animate(self) -> None:
        while self._run_task and not self._run_task.done():
            self._spinner_index += 1
            if self.state.status == "running": self.redraw()
            await asyncio.sleep(0.12)

    async def _close_tasks(self) -> None:
        if self._token: self._token.cancel("TUI closed")
        for task in (self._run_task, self._approval_task, self._animation_task):
            if task: task.cancel()
        await asyncio.gather(*(task for task in (self._run_task, self._approval_task, self._animation_task) if task), return_exceptions=True)


def _read_key() -> str:
    import msvcrt
    key = msvcrt.getwch()
    if key not in {"\x00", "\xe0"}: return key
    return {"K": "left", "M": "right", "G": "home", "O": "end", "H": "up", "P": "down", "S": "delete"}.get(msvcrt.getwch(), "")


def _stdout_write(value: str) -> None: sys.stdout.write(value); sys.stdout.flush()


def render_terminal(state: TerminalState, input_text: str, columns: int, rows: int, **_: object) -> str:
    """Compatibility helper for tests; it never clears or replaces terminal history."""
    return render_entries(state.entries, columns, theme=Theme.SIGNAL, color=ColorMode.NEVER) + "\n" + render_live_tail(input_text, state.status, columns, color=ColorMode.NEVER)
