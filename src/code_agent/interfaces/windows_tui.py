from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import time
from collections.abc import Callable, Sequence
from typing import Optional
from code_agent.core.attachments import AttachmentRef
from code_agent.core.cancellation import CancellationError, CancellationToken
from code_agent.core.events import EventKind
from .controller import AgentController
from .attachment_input import AttachmentDraft, PreparedInput, has_submission_input, prepare_input
from .command_registry import CommandRegistry, REGISTRY
from .history import ThreadHistoryReader, load_thread_history
from .input_buffer import InputBuffer
from .input_events import ExitGuard
from .terminal_display import DisplayKind, text_entry
from .terminal_renderer import ColorMode, Theme, render_entries
from .terminal_io import BRACKETED_PASTE_DISABLE, BRACKETED_PASTE_ENABLE, read_key, render_terminal as render_terminal, stdout_write
from .terminal_status import status_context, status_presentation
from .terminal_tail import LiveTailGeometry, clear_live_tail, render_live_tail_frame
from .terminal_state import ApprovalBroker, ApprovalRequest, TerminalState
from .profile_control import ProfileControl
from .mode_control import ModeControl
from .permission_control import PermissionControl
from code_agent.skills.registry import SkillActivation
from code_agent.mcp.registry import McpRegistry
from .task_controller import ForegroundTaskController
from .tui_commands import ParseOutcome, parse_tui_command
from .i18n import catalog_for, select_runtime_language
from .tui_input import apply_clipboard_images, apply_paste, handle_interrupt
from .command_availability import available_services
from .tui_interactions import TuiInteractions
from .diff_view import GitDiffSource
from .tui_command_dispatch import handle_tui_command
from .interaction import InteractionBroker
from .checkpoint_control import CheckpointControl
from .checkpoint_tui import close_rewind_flow, wait_rewind_task
from .tui_lifecycle import close_tasks, listen_approvals, listen_interactions, start_animation, stop_animation
from .tui_peer_turn import yield_peer_slot
from .tui_submission import SubmitMode, pause_active_task, submit_active_input, toggle_submit_mode
from .tui_protocols import EvidenceReader, SessionBrowser

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

class WindowsTerminalApp:
    """Append-only Windows Terminal interaction without alternate-screen control."""

    def __init__(self, controller: AgentController, approvals: ApprovalBroker, *, sessions: Optional[SessionBrowser] = None, peers: object | None = None, evidence: Optional[EvidenceReader] = None, tasks: ForegroundTaskController | None = None, history: Optional[ThreadHistoryReader] = None, profiles: ProfileControl | None = None, modes: ModeControl | None = None, runtime_selection: object | None = None, permissions: PermissionControl | None = None, skills: SkillActivation | None = None, mcp: McpRegistry | None = None, workflows: object | None = None, plugins: object | None = None, checkpoints: CheckpointControl | None = None, interaction_broker: InteractionBroker | None = None, command_registry: CommandRegistry = REGISTRY, diff_source: GitDiffSource | None = None, attachment_draft: AttachmentDraft | None = None, write: Optional[Callable[[str], object]] = None, project_name: str | None = None) -> None:
        self.controller, self.approvals = controller, approvals
        self.sessions, self.peers, self.evidence, self.tasks, self.history, self.profiles, self.modes, self.runtime_selection, self.permissions, self.skills, self.mcp, self.workflows, self.plugins, self.checkpoints, self.command_registry, self._write = sessions, peers, evidence, tasks, history, profiles, modes, runtime_selection, permissions, skills, mcp, workflows, plugins, checkpoints, command_registry, write or stdout_write
        self.project_name = project_name or Path.cwd().name or "chaos-agent"
        self._last_terminal_title: str | None = None
        self._has_completed_task = False
        self._task_finished_handled = False
        self.state = TerminalState(); self.input = InputBuffer(); self.current_thread_id: str | None = None
        self.exit_guard = ExitGuard()
        self.interaction_broker = interaction_broker
        self._pending_interaction = None
        self._rewind_flow = None
        self._rewind_task: asyncio.Task[None] | None = None
        self._closing = False
        self._interaction_done = asyncio.Event()
        self._interaction_task: asyncio.Task[None] | None = None
        self.interactions = TuiInteractions(diff_source)
        self.attachment_draft = attachment_draft
        self.active_task_id: str | None = None; self.running = False; self._run_task: asyncio.Task[None] | None = None
        self.submit_mode = SubmitMode.QUEUE
        self._peer_run_task: asyncio.Task[None] | None = None
        self._animation_task: asyncio.Task[None] | None = None; self._spinner_index = 0; self._redraw_dirty = True
        self._token: CancellationToken | None = None; self._approval_task: asyncio.Task[None] | None = None
        self._pending_approval: ApprovalRequest | None = None; self._approval_done = asyncio.Event()
        self._flushed_entries = 0
        self._tail_geometry: LiveTailGeometry | None = None
        self._run_started_at: float | None = None; self._drawn_draft_revision = -1; self._drawn_size: tuple[int, int] | None = None; self._next_spinner_at = time.monotonic() + 0.1
        self.theme, self.color = Theme.SYMBOL, ColorMode.AUTO
        self.catalog = catalog_for(select_runtime_language())

    def set_terminal_title(self, title: str) -> None:
        if title != self._last_terminal_title:
            self._last_terminal_title = title
            self._write(f"\x1b]0;{title}\x07")

    def update_terminal_title(self, *, running: bool | None = None) -> None:
        if running is None:
            running = bool(self._run_task and not self._run_task.done())
        if running:
            frame = _SPINNER_FRAMES[self._spinner_index % len(_SPINNER_FRAMES)]
            self.set_terminal_title(f"{frame} {self.project_name}")
        elif self._pending_approval is not None:
            self.set_terminal_title(f"🔔 {self.project_name}")
        elif self._has_completed_task:
            self.set_terminal_title(f"🔔 {self.project_name}")
        else:
            self.set_terminal_title(self.project_name)

    def reset_terminal_title(self) -> None:
        self.set_terminal_title("PowerShell")

    def play_sound(self, *, alert: bool = False) -> None:
        try:
            self._write("\a")
        except Exception:
            pass
        try:
            if os.name == "nt":
                import winsound
                flag = winsound.MB_ICONEXCLAMATION if alert else winsound.MB_ICONASTERISK
                winsound.MessageBeep(flag)
        except Exception:
            pass

    def on_approval_requested(self) -> None:
        self.set_terminal_title(f"🔔 {self.project_name}")
        self.play_sound(alert=True)

    def on_task_finished(self) -> None:
        if self._closing or self._task_finished_handled:
            return
        self._task_finished_handled = True
        if self.state.status == "paused":
            self.set_terminal_title(self.project_name)
            return
        self._has_completed_task = True
        self.set_terminal_title(f"🔔 {self.project_name}")
        self.play_sound(alert=False)

    async def run(self, *, thread_id: str | None = None) -> None:
        if os.name != "nt": raise RuntimeError("WindowsTerminalApp requires Windows")
        if self.tasks:
            await self.tasks.reconcile_stale_tasks()
        if thread_id: await self.restore_thread(thread_id)
        start_peers = getattr(self.peers, "start", None)
        if callable(start_peers):
            await start_peers()
        self.update_terminal_title()
        self._write(BRACKETED_PASTE_ENABLE); self.running = True; self._approval_task = asyncio.create_task(listen_approvals(self))
        if self.interaction_broker is not None:
            self._interaction_task = asyncio.create_task(listen_interactions(self))
        self.redraw()
        try:
            while self.running: await self.handle_key(await asyncio.to_thread(read_key))
        finally:
            self.reset_terminal_title()
            self._write(BRACKETED_PASTE_DISABLE); await close_tasks(self)
    async def submit(
        self,
        text: str,
        *,
        attachments: Sequence[AttachmentRef] | None = None,
    ) -> bool:
        if not isinstance(text, str): raise TypeError("text must be a string")
        if not has_submission_input(self.attachment_draft, text, attachments):
            return False
        if self._pending_approval is not None:
            self._append(DisplayKind.ERROR, "approval decision is pending")
            return False
        if not await yield_peer_slot(self):
            return False
        if text.strip():
            parsed = parse_tui_command(text, available_services(self), self.command_registry)
            if parsed.is_command: return await self._handle_command(parsed)
            if parsed.error: self._append(DisplayKind.ERROR, parsed.error); return False
        try:
            prepared = prepare_input(self.attachment_draft, text, attachments)
        except (RuntimeError, ValueError) as error:
            self._append(DisplayKind.ERROR, str(error)); self.redraw(); return False
        self._append(DisplayKind.USER, prepared.display)
        if self.tasks and self.active_task_id and self._run_task and not self._run_task.done():
            return await submit_active_input(self, prepared)
        self._token = CancellationToken()
        self._run_started_at = time.monotonic()
        self._task_finished_handled = False
        self.update_terminal_title(running=True)
        if self.tasks:
            self.state.begin_run()
            self.redraw()
            if self.active_task_id:
                task_id = self.active_task_id
            else:
                try: record = await self.tasks.start(prepared.prompt)
                except (RuntimeError, ValueError) as error: self._append(DisplayKind.ERROR, str(error)); self.redraw(); return False
                task_id = record.id; self.active_task_id = task_id
            self._run_task = asyncio.create_task(
                self._consume_task(
                    task_id, prepared.prompt, prepared.attachments, prepared
                )
            )
        else:
            self.state.begin_run()
            self.redraw()
            self._run_task = asyncio.create_task(
                self._consume(
                    prepared.prompt, self._token, prepared.attachments, prepared
                )
            )
        self._start_animation(); self.redraw(); return True
    async def wait_idle(self) -> None:
        if self._run_task: await self._run_task
        await stop_animation(self)
    async def wait_checkpoint_idle(self) -> None: await wait_rewind_task(self)
    async def close_checkpoint_flow(self) -> None:
        await close_rewind_flow(self)
    async def handle_key(self, key: str) -> None:
        if key == "\x03":
            await handle_interrupt(self)
        elif await self.interactions.handle_key(self, key):
            pass
        elif key == "\x1b" and await pause_active_task(self, "user requested pause"):
            pass
        elif key.startswith("\x1b[200~") and key.endswith("\x1b[201~"):
            await apply_paste(self, key[6:-6])
        elif key == "\x16": await apply_clipboard_images(self)
        elif key == "\x15": self.input.clear()
        elif key == "\t" and self._run_task and not self._run_task.done(): toggle_submit_mode(self)
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
        now = time.monotonic(); size = shutil.get_terminal_size((100, 30))
        palette = self.interactions.rows(self, max_rows=max(0, size.lines - 4))
        status, icon, status_color = status_presentation(self.state.status, self.state.execution_summary, self.state.active_action, self.catalog.language, self.theme, self._spinner_index)
        if self._run_task and not self._run_task.done(): status += f" [{self.submit_mode.label}]"
        if self.interactions.steering.pending_count: status += " · " + self.interactions.steering.status_line()
        frame = render_live_tail_frame(
            self.input.text,
            status,
            size.columns,
            cursor_index=self.input.cursor,
            assistant_draft=self.state.draft_answer,
            terminal_height=size.lines,
            color=self.color,
            palette=palette,
            status_icon=icon,
            status_color=status_color,
            status_context=status_context(
                self._current_model(),
                self._run_started_at,
                now,
                self.state.token_rate.rate(now) or self.state.last_rate,
                tokens=self.state.total_tokens,
                context_window=self._context_window(),
                branch=self._git_branch(),
            ),
            previous=self._tail_geometry,
            theme=self.theme,
        )
        self._write(frame.text)
        self._tail_geometry = frame.geometry; self._redraw_dirty = False; self._drawn_draft_revision = self.state.draft_revision; self._drawn_size = (size.columns, size.lines)
    async def restore_thread(self, thread_id: str) -> bool:
        if self.history is None: self._append(DisplayKind.ERROR, "session history unavailable"); return False
        try: history = await load_thread_history(self.history, thread_id)
        except Exception: self._append(DisplayKind.ERROR, "session restore failed"); return False
        restored = TerminalState(); restored.restore(history); self.state = restored; self.current_thread_id = thread_id
        height = shutil.get_terminal_size((100, 30)).lines
        self._write(clear_live_tail(self._tail_geometry, terminal_height=height) + render_entries(restored.entries, 100, theme=self.theme, color=self.color) + "\n\r")
        self._tail_geometry = None; self._flushed_entries = len(restored.entries); return True
    async def _consume(
        self,
        text: str,
        token: CancellationToken,
        attachments: tuple[AttachmentRef, ...] = (),
        submitted: PreparedInput | None = None,
    ) -> None:
        try:
            async for event in self.controller.ask(
                text,
                thread_id=self.current_thread_id,
                cancellation=token,
                attachments=attachments,
            ):
                self.state.apply(event)
                submitted = self._acknowledge_submission(event.kind, submitted)
                if event.kind is not EventKind.MODEL_EVENT:
                    self._flush_pending_entries()
                if self.state.thread_id: self.current_thread_id = self.state.thread_id
                self._request_redraw(immediate=event.kind is not EventKind.MODEL_EVENT)
        except CancellationError:
            self.state.status = "paused"
        except Exception as error:
            import traceback
            traceback.print_exc()
            self._append(DisplayKind.ERROR, f"{type(error).__name__}: {error}")
        finally:
            self.on_task_finished()
            self._request_redraw(immediate=True)
    async def _consume_task(
        self,
        task_id: str,
        text: str,
        attachments: tuple[AttachmentRef, ...] = (),
        submitted: PreparedInput | None = None,
    ) -> None:
        if not self.tasks: return
        terminal = False
        try:
            stream = (
                self.tasks.events(task_id, text, attachments=attachments)
                if attachments
                else self.tasks.events(task_id, text)
            )
            async for event in stream:
                self.state.apply(event)
                submitted = self._acknowledge_submission(event.kind, submitted)
                if self.state.thread_id:
                    self.current_thread_id = self.state.thread_id
                self.interactions.observe_event(self, event)
                terminal = terminal or event.kind is EventKind.COMPLETED or (
                    event.kind is EventKind.TASK_STATUS_CHANGED
                    and event.payload.get("status") in {"completed", "accepted_partial", "failed"}
                )
                if event.kind is not EventKind.MODEL_EVENT:
                    self._flush_pending_entries()
                self._request_redraw(immediate=event.kind is not EventKind.MODEL_EVENT)
        except CancellationError:
            self.state.status = "paused"
            self._append(DisplayKind.METADATA, "task paused")
        except Exception as error:
            self.active_task_id = None
            self._append(DisplayKind.ERROR, f"{type(error).__name__}: {error}")
        finally:
            self.on_task_finished()
            if (terminal or self.state.status in {"failed", "error"}) and self.active_task_id == task_id:
                self.active_task_id = None
            self._request_redraw(immediate=True)
    async def _handle_command(self, outcome: ParseOutcome) -> bool:
        return await handle_tui_command(self, outcome)
    def _current_model(self) -> str | None:
        control = self.runtime_selection or self.modes
        if control is not None: return control.current.model
        return self.profiles.current.model if self.profiles else None

    def _git_branch(self) -> str | None:
        try:
            head_path = Path(".git/HEAD")
            if head_path.exists():
                content = head_path.read_text(encoding="utf-8", errors="ignore").strip()
                if content.startswith("ref: refs/heads/"):
                    return content[len("ref: refs/heads/"):] + "*"
                return "detached"
        except Exception:
            pass
        return None

    def _context_window(self) -> int:
        if self.profiles and hasattr(self.profiles, "_profiles"):
            curr = getattr(self.profiles, "_current", None)
            if curr and curr in self.profiles._profiles:
                profile = self.profiles._profiles[curr]
                return getattr(profile, "context_window", 128_000)
        return 128_000
    def _append(self, kind: DisplayKind, value: object) -> None:
        self.state.entries.append(text_entry(kind, value)); self.state.transcript.append(self.state.entries[-1].text); self._flush_pending_entries()
    def _flush_pending_entries(self) -> None:
        new = self.state.entries[self._flushed_entries:]
        if new:
            previous = self.state.entries[self._flushed_entries - 1] if self._flushed_entries else None
            rendered = render_entries(new, self._columns(), theme=self.theme, color=self.color, previous=previous)
            height = shutil.get_terminal_size((100, 30)).lines
            self._write(clear_live_tail(self._tail_geometry, terminal_height=height) + rendered + "\n\r")
            self._tail_geometry = None
            self._flushed_entries = len(self.state.entries)

    def _columns(self) -> int:
        return shutil.get_terminal_size((100, 30)).columns
    def _request_redraw(self, *, immediate: bool = False) -> None:
        self._redraw_dirty = True
        if immediate: self.redraw()
    def _start_animation(self) -> None:
        start_animation(self)
    def _acknowledge_submission(self, kind: EventKind, submitted: PreparedInput | None) -> PreparedInput | None:
        if kind is EventKind.MESSAGE_ADDED and submitted is not None and submitted.from_draft and self.attachment_draft is not None:
            self.attachment_draft.commit(submitted.attachments)
        return None if kind is EventKind.MESSAGE_ADDED else submitted
