from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Callable, Sequence
from typing import Optional, Protocol

from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.events import EventKind
from .controller import AgentController
from .command_registry import REGISTRY
from .history import ThreadHistoryReader, load_thread_history
from .input_buffer import InputBuffer
from .input_events import ExitGuard
from .terminal_display import DisplayKind, text_entry
from .terminal_renderer import ColorMode, Theme, render_entries
from .terminal_io import BRACKETED_PASTE_DISABLE, BRACKETED_PASTE_ENABLE, read_key, render_terminal, stdout_write
from .terminal_status import status_context, status_presentation, status_snapshot
from .terminal_tail import LiveTailGeometry, clear_live_tail, render_live_tail_frame
from .terminal_state import ApprovalBroker, ApprovalRequest, TerminalState
from .profile_control import ProfileControl
from code_agent.skills.registry import SkillActivation
from code_agent.mcp.registry import McpRegistry
from .task_controller import ForegroundTaskController
from .tui_commands import ParseOutcome, TuiCommandKind, parse_tui_command
from .i18n import catalog_for, localize_task_status, select_runtime_language
from .evidence_view import format_evidence_summary
from .tui_input import apply_paste, handle_interrupt
from .command_availability import available_services
from .tui_builtin_commands import handle_builtin_command
from .tui_display_commands import handle_display_command
from .tui_mcp_commands import handle_mcp_command
from .tui_interactions import TuiInteractions
from .diff_view import GitDiffSource
class SessionBrowser(Protocol):
    async def list_threads(self, *, limit: int = 100) -> Sequence[object]: ...

class EvidenceReader(Protocol):
    async def list_verification_evidence(self, task_id: str) -> Sequence[object]: ...
class WindowsTerminalApp:
    """Append-only Windows Terminal interaction without alternate-screen control."""

    def __init__(self, controller: AgentController, approvals: ApprovalBroker, *, sessions: Optional[SessionBrowser] = None, evidence: Optional[EvidenceReader] = None, tasks: ForegroundTaskController | None = None, history: Optional[ThreadHistoryReader] = None, profiles: ProfileControl | None = None, skills: SkillActivation | None = None, mcp: McpRegistry | None = None, diff_source: GitDiffSource | None = None, write: Optional[Callable[[str], object]] = None) -> None:
        self.controller, self.approvals = controller, approvals
        self.sessions, self.evidence, self.tasks, self.history, self.profiles, self.skills, self.mcp, self._write = sessions, evidence, tasks, history, profiles, skills, mcp, write or stdout_write
        self.state = TerminalState(); self.input = InputBuffer(); self.current_thread_id: str | None = None
        self.exit_guard = ExitGuard()
        self.interactions = TuiInteractions(diff_source)
        self.active_task_id: str | None = None; self.running = False; self._run_task: asyncio.Task[None] | None = None
        self._animation_task: asyncio.Task[None] | None = None; self._spinner_index = 0
        self._token: CancellationToken | None = None; self._approval_task: asyncio.Task[None] | None = None
        self._pending_approval: ApprovalRequest | None = None; self._approval_done = asyncio.Event()
        self._flushed_entries = 0
        self._tail_geometry: LiveTailGeometry | None = None
        self._run_started_at: float | None = None
        self.theme, self.color = Theme.SYMBOL, ColorMode.AUTO
        self.catalog = catalog_for(select_runtime_language())
    async def run(self, *, thread_id: str | None = None) -> None:
        if os.name != "nt": raise RuntimeError("WindowsTerminalApp requires Windows")
        if self.tasks:
            await self.tasks.reconcile_stale_tasks()
        if thread_id: await self.restore_thread(thread_id)
        self._write(BRACKETED_PASTE_ENABLE); self.running = True; self._approval_task = asyncio.create_task(self._listen_approvals()); self.redraw()
        try:
            while self.running: await self.handle_key(await asyncio.to_thread(read_key))
        finally: self._write(BRACKETED_PASTE_DISABLE); await self._close_tasks()
    async def submit(self, text: str) -> bool:
        if not isinstance(text, str): raise TypeError("text must be a string")
        if not text.strip(): return False
        if self._pending_approval is not None:
            self._append(DisplayKind.ERROR, "approval decision is pending")
            return False
        parsed = parse_tui_command(text, available_services(self))
        if parsed.is_command: return await self._handle_command(parsed)
        if parsed.error: self._append(DisplayKind.ERROR, parsed.error); return False
        self._append(DisplayKind.USER, text)
        if self.tasks and self.active_task_id and self._run_task and not self._run_task.done():
            await self.interactions.steer(self, self.active_task_id, text)
            self.redraw()
            return True
        self._token = CancellationToken()
        if self.tasks:
            if self.active_task_id:
                task_id = self.active_task_id
            else:
                try: record = await self.tasks.start(text)
                except RuntimeError as error: self._append(DisplayKind.ERROR, str(error)); self.redraw(); return False
                task_id = record.id; self.active_task_id = task_id
            self.state.begin_run(); self._run_started_at = time.monotonic()
            self._run_task = asyncio.create_task(self._consume_task(task_id, text))
        else: self._run_task = asyncio.create_task(self._consume(text, self._token))
        self._start_animation()
        self.redraw(); return True

    async def wait_idle(self) -> None:
        if self._run_task: await self._run_task
        await self._stop_animation()

    async def handle_key(self, key: str) -> None:
        if key == "\x03":
            await handle_interrupt(self)
        elif await self.interactions.handle_key(self, key):
            pass
        elif key == "\x1b" and self.tasks and self.active_task_id:
            await self.tasks.pause(self.active_task_id, "user requested pause")
        elif key.startswith("\x1b[200~") and key.endswith("\x1b[201~"):
            apply_paste(self, key[6:-6])
        elif key == "\x15": self.input.clear()
        elif key == "\r": await self.submit(self.input.submit())
        elif key == "\n": self.input.insert_line_break()
        elif key == "left": self.input.move_left()
        elif key == "right": self.input.move_right()
        elif key == "home": self.input.move_home()
        elif key == "end": self.input.move_end()
        elif key == "up" and not self.input.move_up(): self.input.previous()
        elif key == "down" and not self.input.move_down(): self.input.next()
        elif key in {"\x08", "\x7f"}: self.input.backspace()
        elif key == "delete": self.input.delete()
        elif key.isprintable(): self.exit_guard.input_received(); self.input.insert(key)
        self.redraw()

    def redraw(self) -> None:
        palette = self.interactions.rows(self)
        status, icon, status_color = status_presentation(self.state.status, self.state.execution_summary, self.state.active_action, self.catalog.language, self.theme, self._spinner_index)
        if self.interactions.steering.pending_count: status += " · " + self.interactions.steering.status_line()
        frame = render_live_tail_frame(
            self.input.text,
            status,
            self._columns(),
            cursor_index=self.input.cursor,
            color=self.color,
            palette=palette,
            status_icon=icon,
            status_color=status_color,
            status_context=status_context(self.profiles.current.model if self.profiles else None, self._run_started_at, time.monotonic()),
            previous=self._tail_geometry,
        )
        self._write(frame.text)
        self._tail_geometry = frame.geometry

    async def restore_thread(self, thread_id: str) -> bool:
        if self.history is None: self._append(DisplayKind.ERROR, "session history unavailable"); return False
        try: history = await load_thread_history(self.history, thread_id)
        except Exception: self._append(DisplayKind.ERROR, "session restore failed"); return False
        restored = TerminalState(); restored.restore(history); self.state = restored; self.current_thread_id = thread_id
        self._write(clear_live_tail(self._tail_geometry) + render_entries(restored.entries, 100, theme=self.theme, color=self.color) + "\n\r")
        self._tail_geometry = None; self._flushed_entries = len(restored.entries); return True

    async def _consume(self, text: str, token: CancellationToken) -> None:
        try:
            async for event in self.controller.ask(text, thread_id=self.current_thread_id, cancellation=token):
                self.state.apply(event)
                if event.kind is not EventKind.MODEL_EVENT:
                    self._flush_pending_entries()
                if self.state.thread_id: self.current_thread_id = self.state.thread_id
                self.redraw()
        except CancellationError:
            self.state.status = "paused"

    async def _consume_task(self, task_id: str, text: str) -> None:
        if not self.tasks: return
        terminal = False
        try:
            async for event in self.tasks.events(task_id, text):
                self.state.apply(event)
                self.interactions.observe_event(self, event.kind)
                terminal = terminal or event.kind is EventKind.COMPLETED or (
                    event.kind is EventKind.TASK_STATUS_CHANGED
                    and event.payload.get("status") in {"completed", "accepted_partial", "failed"}
                )
                if event.kind is not EventKind.MODEL_EVENT:
                    self._flush_pending_entries()
                self.redraw()
        except CancellationError:
            self.state.status = "paused"
            self._append(DisplayKind.METADATA, "task paused")
        except Exception as error: self._append(DisplayKind.ERROR, type(error).__name__)
        finally:
            if terminal and self.active_task_id == task_id: self.active_task_id = None

    async def _handle_command(self, outcome: ParseOutcome) -> bool:
        command = outcome.command
        assert command is not None
        builtin = await handle_builtin_command(self, command)
        if builtin is not None: return builtin
        display = await handle_display_command(self, command)
        if display is not None: return display
        if command.kind is TuiCommandKind.DIFF: await self.interactions.show_diff(self)
        elif command.kind is TuiCommandKind.STATUS:
            self._append(DisplayKind.METADATA, status_snapshot(self.state.status, self.active_task_id or self.state.task_id, self.current_thread_id, self.profiles.current.model if self.profiles else None))
        elif command.kind is TuiCommandKind.HELP: self._append(DisplayKind.METADATA, " ".join(item.display for item in REGISTRY.available(available_services(self))))
        elif command.kind is TuiCommandKind.MODEL:
            if self.profiles is None: self._append(DisplayKind.ERROR, "model profiles are unavailable"); return False
            if command.instruction in {None, "列表", "list"}:
                self._append(DisplayKind.METADATA, " | ".join(f"{item.name}:{item.model}" for item in self.profiles.list()))
            elif command.instruction.startswith("使用 ") or command.instruction.startswith("use "):
                name = command.instruction.split(maxsplit=1)[1]
                try: selected = await self.profiles.use(name, idle=self._run_task is None or self._run_task.done())
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
        elif command.kind is TuiCommandKind.MCP: return await handle_mcp_command(self, command.instruction)
        elif command.kind is TuiCommandKind.EVIDENCE:
            task_id = self.active_task_id or self.state.task_id
            if self.evidence is None or not task_id:
                self._append(DisplayKind.ERROR, "evidence is unavailable"); return False
            self._append(DisplayKind.METADATA, format_evidence_summary(await self.evidence.list_verification_evidence(task_id)))
        elif self.tasks and command.kind is TuiCommandKind.TASKS:
            records = await self.tasks.list(include_terminal=True); self._append(DisplayKind.METADATA, " | ".join(f"{item.id}:{localize_task_status(item.status.value, self.catalog)}" for item in records))
        elif self.tasks and command.kind in {TuiCommandKind.PAUSE, TuiCommandKind.STOP, TuiCommandKind.ACCEPT, TuiCommandKind.RESUME, TuiCommandKind.STEER}:
            task_id = command.task_id or self.active_task_id
            if not task_id: self._append(DisplayKind.ERROR, "no active task"); return False
            if command.kind is TuiCommandKind.PAUSE: await self.tasks.pause(task_id)
            elif command.kind is TuiCommandKind.STOP: await self.tasks.stop(task_id)
            elif command.kind is TuiCommandKind.ACCEPT: await self.tasks.accept_partial(task_id, command.instruction or "user accepted partial delivery")
            elif command.kind is TuiCommandKind.STEER: await self.interactions.steer(self, task_id, command.instruction or "")
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
            previous = self.state.entries[self._flushed_entries - 1] if self._flushed_entries else None
            rendered = render_entries(new, self._columns(), theme=self.theme, color=self.color, previous=previous)
            self._write(clear_live_tail(self._tail_geometry) + rendered + "\n\r")
            self._tail_geometry = None
            self._flushed_entries = len(self.state.entries)

    def _columns(self) -> int:
        return shutil.get_terminal_size((100, 30)).columns

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
        if self._pending_approval is not None:
            self.approvals.resolve(self._pending_approval.request_id, False)
            self._pending_approval = None
            self._approval_done.set()
        if self.tasks and self.active_task_id:
            await self.tasks.interrupt(self.active_task_id, "TUI closed")
        if self._token: self._token.cancel("TUI closed")
        for task in (self._run_task, self._approval_task, self._animation_task):
            if task: task.cancel()
        await asyncio.gather(*(task for task in (self._run_task, self._approval_task, self._animation_task) if task), return_exceptions=True)
