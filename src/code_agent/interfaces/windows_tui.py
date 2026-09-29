from __future__ import annotations

import asyncio
from collections import deque
import hashlib
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
from .attachment_input import AttachmentDraft, PreparedInput
from .command_registry import CommandRegistry, REGISTRY
from .history import ThreadHistoryReader, load_thread_history
from .input_buffer import InputBuffer
from .input_events import ExitGuard
from .terminal_display import DisplayKind, text_entry
from .terminal_renderer import ColorMode, Theme, render_entries
from .terminal_size import terminal_size
from .terminal_win32_input import WIN32_INPUT_ENABLE, WIN32_INPUT_DISABLE
from .terminal_io import (
    BRACKETED_PASTE_DISABLE, BRACKETED_PASTE_ENABLE, MOUSE_REPORT_DISABLE,
    MOUSE_REPORT_ENABLE, capture_ctrl_c_as_input, read_key,
    render_terminal as render_terminal, stdout_write,
)
from .terminal_tail import LiveTailGeometry, clear_live_tail
from .terminal_state import ApprovalBroker, ApprovalRequest, TerminalState
from .profile_control import ProfileControl
from .mode_control import ModeControl
from .permission_control import PermissionControl
from code_agent.skills.registry import SkillActivation
from code_agent.mcp.registry import McpRegistry
from .task_controller import ForegroundTaskController
from .tui_commands import ParseOutcome
from .i18n import catalog_for, select_runtime_language
from .tui_input import (
    apply_clipboard_images, apply_paste, clear_input, delete_input,
    handle_interrupt, insert_input, sync_attachment_input,
)
from .tui_interactions import TuiInteractions
from .diff_view import GitDiffSource
from .tui_command_dispatch import handle_tui_command
from .interaction import InteractionBroker
from .checkpoint_control import CheckpointControl
from .checkpoint_tui import close_rewind_flow, wait_rewind_task
from .tui_lifecycle import close_tasks, listen_approvals, listen_interactions, start_animation, stop_animation
from .tui_submission import SubmitMode, pause_active_task, toggle_submit_mode
from .tui_protocols import EvidenceReader, SessionBrowser
from .tui_run import submit as submit_input, start_prepared, finish_run
from .tui_auth_prompt import handle_auth_key
from code_agent.core.debug_trace import trace_event

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

from .tui_presentation import TerminalPresentation
from .terminal_motion import TailMotion, watch_visuals
from .terminal_theme import design_for


class WindowsTerminalApp(TerminalPresentation):
    """Append-only terminal interaction without alternate-screen control.

    The public class name is retained for compatibility while Windows and POSIX
    terminal input are selected by the terminal-I/O layer.
    """

    def __init__(self, controller: AgentController, approvals: ApprovalBroker, *, sessions: Optional[SessionBrowser] = None, peers: object | None = None, evidence: Optional[EvidenceReader] = None, tasks: ForegroundTaskController | None = None, history: Optional[ThreadHistoryReader] = None, profiles: ProfileControl | None = None, modes: ModeControl | None = None, runtime_selection: object | None = None, task_modes: object | None = None, permissions: PermissionControl | None = None, costs: object | None = None, doctor: object | None = None, skills: SkillActivation | None = None, mcp: McpRegistry | None = None, workflows: object | None = None, plugins: object | None = None, checkpoints: CheckpointControl | None = None, interaction_broker: InteractionBroker | None = None, command_registry: CommandRegistry = REGISTRY, diff_source: GitDiffSource | None = None, attachment_draft: AttachmentDraft | None = None, write: Optional[Callable[[str], object]] = None, project_name: str | None = None, workspace_root: Path | None = None) -> None:
        self.controller, self.approvals = controller, approvals
        self.sessions, self.peers, self.evidence, self.tasks, self.history, self.profiles, self.modes, self.runtime_selection, self.permissions, self.skills, self.mcp, self.workflows, self.plugins, self.checkpoints, self.command_registry, self._write = sessions, peers, evidence, tasks, history, profiles, modes, runtime_selection, permissions, skills, mcp, workflows, plugins, checkpoints, command_registry, write or stdout_write
        self.task_modes, self.costs, self.doctor = task_modes, costs, doctor
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
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
        self._pause_task: asyncio.Task[None] | None = None
        self._animation_task: asyncio.Task[None] | None = None; self._spinner_index = 0; self._redraw_dirty = True
        self._token: CancellationToken | None = None; self._approval_task: asyncio.Task[None] | None = None
        self._pending_approval: ApprovalRequest | None = None; self._approval_done = asyncio.Event()
        self._flushed_entries = 0
        self._tail_geometry: LiveTailGeometry | None = None
        self._projection_epoch = 0
        self.composer_expanded: bool = True
        self._run_started_at: float | None = None; self._drawn_draft_revision = -1; self._drawn_size: tuple[int, int] | None = None; self._next_spinner_at = time.monotonic() + 0.1
        self._last_alt_v_failure_at = 0.0
        self._viewport_offset = 0
        self._viewport_needs_full_redraw = False
        self.theme, self.color = Theme.SLATE, ColorMode.AUTO
        self.motion = TailMotion()
        self._visual_task = None
        self._pending_input_keys: deque[str] = deque()
        self.catalog = catalog_for(select_runtime_language())

    async def run(self, *, thread_id: str | None = None) -> None:
        if self.tasks:
            await self.tasks.reconcile_stale_tasks()
        if thread_id: await self.restore_thread(thread_id)
        start_peers = getattr(self.peers, "start", None)
        if callable(start_peers):
            await start_peers()
        self.update_terminal_title()
        restore_ctrl_c = capture_ctrl_c_as_input()
        platform_input_enable = WIN32_INPUT_ENABLE if os.name == "nt" else ""
        platform_input_disable = WIN32_INPUT_DISABLE if os.name == "nt" else ""
        try:
            self._write("\x1b[6 q" + platform_input_enable + BRACKETED_PASTE_ENABLE + MOUSE_REPORT_ENABLE); self.running = True; self._approval_task = asyncio.create_task(listen_approvals(self))
            if self.interaction_broker is not None:
                self._interaction_task = asyncio.create_task(listen_interactions(self))
            self.redraw()
            self._visual_task = asyncio.create_task(watch_visuals(self))
            while self.running:
                key = (
                    self._pending_input_keys.popleft()
                    if self._pending_input_keys
                    else await asyncio.to_thread(read_key, timeout=.1)
                )
                if key in {"scroll_up", "scroll_down"}:
                    self.scroll_viewport(await self._read_scroll_burst(key))
                    self.redraw()
                elif key is not None:
                    await self.handle_key(key)
        finally:
            try:
                self.reset_terminal_title()
                self._write(BRACKETED_PASTE_DISABLE + MOUSE_REPORT_DISABLE + platform_input_disable + "\x1b[0 q"); await close_tasks(self)
            finally:
                restore_ctrl_c()
    async def submit(
        self,
        text: str,
        *,
        attachments: Sequence[AttachmentRef] | None = None,
        skill_id: str | None = None,
        skill_ids: tuple[str, ...] | None = None,
        _skill_prompt: bool = False,
    ) -> bool:
        return await submit_input(
            self, text, attachments, skill_id=skill_id, skill_ids=skill_ids,
            _skill_prompt=_skill_prompt,
        )

    async def _start_prepared(self, prepared: PreparedInput) -> bool:
        return await start_prepared(self, prepared)
    async def wait_idle(self) -> None:
        if self._run_task: await self._run_task
        await stop_animation(self)
        if self._run_task: finish_run(self, self._run_task)
    async def wait_checkpoint_idle(self) -> None: await wait_rewind_task(self)
    async def close_checkpoint_flow(self) -> None:
        await close_rewind_flow(self)
    async def handle_key(self, key: str) -> None:
        if await handle_auth_key(self, key):
            self.redraw()
            return
        sync_attachment_input(self)
        if key == "\x03":
            await handle_interrupt(self)
        elif await self.interactions.handle_key(self, key):
            pass
        elif key in {"\x1b", "escape"}:
            if self.composer_expanded:
                self.composer_expanded = False
                self._clear_input_tail()
            else:
                await pause_active_task(self, "user requested pause")
        elif key.startswith("\x1b[200~") and key.endswith("\x1b[201~"):
            self.composer_expanded = True
            await apply_paste(self, key[6:-6])
        elif key == "alt+v":
            # Some Windows Terminal/ConPTY configurations repeat the Alt
            # chord while the modifier is held. Treat that burst as one paste
            # gesture so an empty clipboard cannot spam identical errors.
            now = time.monotonic()
            if now - self._last_alt_v_failure_at < 0.45:
                return
            self.composer_expanded = True
            if not await apply_clipboard_images(self):
                self._last_alt_v_failure_at = now
            else:
                self._last_alt_v_failure_at = 0.0
        elif key == "\x16":
            self.composer_expanded = True
            # Ctrl+V can be replayed by ConPTY just like Alt+V. Debounce only
            # failed clipboard gestures; successful pastes remain repeatable.
            now = time.monotonic()
            if now - self._last_alt_v_failure_at < 0.45:
                return
            if not await apply_clipboard_images(self):
                self._last_alt_v_failure_at = now
            else:
                self._last_alt_v_failure_at = 0.0
        elif key == "\x15": clear_input(self)
        elif key in {"scroll_up", "page_up"}:
            self.scroll_viewport(1 if key == "scroll_up" else 4)
        elif key in {"scroll_down", "page_down"}:
            self.scroll_viewport(-1 if key == "scroll_down" else -4)
        elif key == "home" and not self.composer_expanded:
            self.scroll_to_history_start()
        elif key == "end" and not self.composer_expanded:
            self.scroll_to_bottom()
        elif key == "\t" and self._run_task and not self._run_task.done(): toggle_submit_mode(self)
        elif key == " " and not self.composer_expanded:
            self.composer_expanded = True
            if self._tail_geometry is not None:
                height = shutil.get_terminal_size((100, 30)).lines
                self._write(self._tail_clear_sequence())
                self._tail_geometry = None
        elif key == "\r":
            if not self.composer_expanded:
                self.composer_expanded = True
                self._clear_input_tail()
            else:
                await self.submit(self.input.submit())
        elif key in {"\n", "shift+enter"}:
            self.composer_expanded = True
            insert_input(self, "\n")
        elif key == "left": self.input.move_left()
        elif key == "right": self.input.move_right()
        elif key == "home": self.input.move_home()
        elif key == "end": self.input.move_end()
        elif key == "up" and not self.input.move_up(): self.input.previous()
        elif key == "down" and not self.input.move_down(): self.input.next()
        elif key in {"\x08", "\x7f"}:
            delete_input(self, backwards=True)
        elif key == "delete":
            delete_input(self, backwards=False)
        elif key.isprintable() and self.composer_expanded:
            self.exit_guard.input_received(); insert_input(self, key)
        self.redraw()

    async def _read_scroll_burst(self, first: str) -> int:
        """Coalesce one frame of wheel reports before repainting the viewport."""
        delta = 1 if first == "scroll_up" else -1
        deadline = asyncio.get_running_loop().time() + (1 / 30)
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            key = await asyncio.to_thread(read_key, timeout=remaining)
            if key is None:
                break
            if key == "scroll_up":
                delta += 1
            elif key == "scroll_down":
                delta -= 1
            else:
                self._pending_input_keys.append(key)
                break
        return delta
    def _clear_input_tail(self) -> None:
        if self._tail_geometry is None: return
        height = shutil.get_terminal_size((100, 30)).lines
        self._write(self._tail_clear_sequence())
        self._tail_geometry = None
    async def restore_thread(self, thread_id: str) -> bool:
        if self.history is None: self._append(DisplayKind.ERROR, "session history unavailable"); return False
        previous_thread_id = self.current_thread_id
        self._projection_epoch += 1
        epoch = self._projection_epoch
        trace_event(
            "tui.thread",
            "restore_started",
            thread_id=thread_id,
            previous_thread_id=previous_thread_id,
            projection_epoch=epoch,
            entries_before=len(self.state.entries),
        )
        try:
            history = await load_thread_history(self.history, thread_id)
            restored = TerminalState()
            restored.restore(history)
            restore_settings = getattr(self.tasks, "restore_runtime_settings", None)
            if restored.task_id and callable(restore_settings):
                await restore_settings(restored.task_id)
        except Exception as error:
            trace_event(
                "tui.thread",
                "restore_failed",
                thread_id=thread_id,
                projection_epoch=epoch,
                error_type=type(error).__name__,
            )
            self._append(DisplayKind.ERROR, "session restore failed"); return False
        self.state = restored; self.current_thread_id = thread_id
        self._pending_skill_id = None; self._pending_skill_ids = None
        self.active_task_id = restored.task_id if restored.task_status not in {None, "completed", "failed", "accepted_partial", "superseded"} else None
        height = shutil.get_terminal_size((100, 30)).lines
        self._write("\x1b[3J\x1b[2J\x1b[H" + render_entries(restored.entries, 100, theme=self.theme, color=self.color) + "\n\r")
        self._tail_geometry = None; self._flushed_entries = len(restored.entries)
        trace_event(
            "tui.thread",
            "restore_completed",
            thread_id=thread_id,
            projection_epoch=epoch,
            entries_after=len(restored.entries),
            transcript_chars=sum(len(entry.text) for entry in restored.entries),
        )
        return True
    async def _consume(
        self,
        text: str,
        token: CancellationToken,
        attachments: tuple[AttachmentRef, ...] = (),
        submitted: PreparedInput | None = None,
    ) -> None:
        epoch = self._projection_epoch
        try:
            async for event in self.controller.ask(
                text,
                thread_id=self.current_thread_id,
                cancellation=token,
                attachments=attachments,
            ):
                if epoch != self._projection_epoch:
                    trace_event(
                        "tui.event",
                        "dropped_stale",
                        source="controller",
                        event_kind=event.kind.value,
                        event_thread_id=event.payload.get("thread_id"),
                        current_thread_id=self.current_thread_id,
                        projection_epoch=epoch,
                        current_projection_epoch=self._projection_epoch,
                    )
                    break
                before = len(self.state.entries)
                self.state.apply(event)
                self._trace_applied_event(event, before, source="controller", epoch=epoch)
                submitted = self._acknowledge_submission(event.kind, submitted)
                if event.kind is not EventKind.MODEL_EVENT:
                    self._flush_pending_entries()
                if self.state.thread_id: self.current_thread_id = self.state.thread_id
                self._request_redraw(immediate=event.kind is not EventKind.MODEL_EVENT)
        except CancellationError:
            self.state.status = "paused"
        except Exception as error:
            from .task_controller import _is_recoverable_model_failure
            recoverable = _is_recoverable_model_failure(error)
            self.state.status = "interrupted" if recoverable else "error"
            from .runtime_errors import explain_runtime_error
            self._append(
                DisplayKind.ERROR,
                explain_runtime_error(
                    error,
                    status=self.state.status,
                    changed=bool(self.state.diff),
                ),
            )
        finally:
            if epoch == self._projection_epoch:
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
        epoch = self._projection_epoch
        terminal = False
        try:
            stream = (
                self.tasks.events(task_id, text, attachments=attachments)
                if attachments
                else self.tasks.events(task_id, text)
            )
            async for event in stream:
                if epoch != self._projection_epoch:
                    trace_event(
                        "tui.event",
                        "dropped_stale",
                        source="task",
                        task_id=task_id,
                        event_kind=event.kind.value,
                        event_thread_id=event.payload.get("thread_id"),
                        current_thread_id=self.current_thread_id,
                        projection_epoch=epoch,
                        current_projection_epoch=self._projection_epoch,
                    )
                    break
                before = len(self.state.entries)
                self.state.apply(event)
                self._trace_applied_event(event, before, source="task", epoch=epoch, task_id=task_id)
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
            from .task_controller import _is_recoverable_model_failure
            recoverable = _is_recoverable_model_failure(error)
            self.state.status = "interrupted" if recoverable else "error"
            from .runtime_errors import explain_runtime_error
            self._append(
                DisplayKind.ERROR,
                explain_runtime_error(
                    error,
                    status=self.state.status,
                    changed=bool(self.state.diff),
                ),
            )
        finally:
            if epoch == self._projection_epoch:
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

    def _append(self, kind: DisplayKind, value: object) -> None:
        self.state.entries.append(text_entry(kind, value)); self.state.transcript.append(self.state.entries[-1].text); self._flush_pending_entries()
    def _flush_pending_entries(self) -> None:
        new = self.state.entries[self._flushed_entries:]
        if new:
            if self._viewport_offset:
                self._flushed_entries = len(self.state.entries)
                self._viewport_needs_full_redraw = True
                self.redraw()
                return
            previous = self.state.entries[self._flushed_entries - 1] if self._flushed_entries else None
            rendered = render_entries(new, self._columns(), theme=self.theme, color=self.color, previous=previous)
            height = shutil.get_terminal_size((100, 30)).lines
            self._write(self._tail_clear_sequence() + rendered + "\n\r")
            self._tail_geometry = None
            self._flushed_entries = len(self.state.entries)
            trace_event(
                "tui.render",
                "flushed",
                thread_id=self.current_thread_id or self.state.thread_id,
                projection_epoch=self._projection_epoch,
                entries_before=self._flushed_entries - len(new),
                entries_after=self._flushed_entries,
                rendered_entries=len(new),
                rendered_chars=sum(len(entry.text) for entry in new),
            )

    def _trace_applied_event(
        self,
        event: object,
        entries_before: int,
        *,
        source: str,
        epoch: int,
        task_id: str | None = None,
    ) -> None:
        payload = getattr(event, "payload", {})
        message = payload.get("message") if isinstance(payload, dict) else None
        content = message.get("content", "") if isinstance(message, dict) else ""
        trace_event(
            "tui.event",
            "applied",
            source=source,
            thread_id=self.current_thread_id or self.state.thread_id,
            task_id=task_id,
            projection_epoch=epoch,
            event_kind=getattr(getattr(event, "kind", None), "value", None),
            event_turn=payload.get("turn") if isinstance(payload, dict) else None,
            durable_sequence=payload.get("sequence") if isinstance(payload, dict) else None,
            message_role=message.get("role") if isinstance(message, dict) else None,
            message_chars=len(content),
            message_digest=hashlib.sha256(content.encode("utf-8")).hexdigest()[:16] if content else None,
            entries_before=entries_before,
            entries_after=len(self.state.entries),
            transcript_chars=sum(len(value) for value in self.state.transcript),
        )

    def _collapse_completed_transcript(self) -> None:
        """Rewrite the finished transcript with consecutive tool calls folded."""
        if not design_for(self.theme) or not self.state.entries:
            return
        rendered = render_entries(
            self.state.entries,
            self._columns(),
            theme=self.theme,
            color=self.color,
            fold_tools=True,
        )
        # A completed run is the one intentional full-transcript rewrite; it
        # removes the detailed streamed rows so the user keeps a compact result.
        self._write(self._tail_clear_sequence() + "\x1b[3J\x1b[2J\x1b[H" + rendered + "\n\r")
        self._tail_geometry = None
        self._flushed_entries = len(self.state.entries)

    def _columns(self) -> int:
        columns = terminal_size((100, 30)).columns
        return max(20, columns - 2) if design_for(self.theme) else columns
    def _request_redraw(self, *, immediate: bool = False) -> None:
        self._redraw_dirty = True
        if immediate: self.redraw()
    def _start_animation(self) -> None:
        start_animation(self)
    def _acknowledge_submission(self, kind: EventKind, submitted: PreparedInput | None) -> PreparedInput | None:
        if kind is EventKind.MESSAGE_ADDED and submitted is not None and submitted.from_draft and self.attachment_draft is not None:
            self.attachment_draft.commit(submitted.attachments)
        return None if kind is EventKind.MESSAGE_ADDED else submitted
