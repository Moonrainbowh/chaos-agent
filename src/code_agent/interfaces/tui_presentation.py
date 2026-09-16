from __future__ import annotations

import os
import shutil  # retained as the patch seam for resize/reflow tests
import time
from pathlib import Path
from .terminal_motion import motion_allowed
from .terminal_theme import design_for
from .terminal_tail import clear_live_tail, get_console_dock_padding, render_live_tail_frame
from .terminal_tail_geometry import resized_tail_geometry
from .terminal_status import status_presentation, status_context
from .terminal_renderer import render_entries
from .tui_input import sync_attachment_input
from .tui_auth_prompt import auth_input_view
from .terminal_display import safe_text, clip_display
from .terminal_size import terminal_size

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _presentation_terminal_size(fallback=(100, 30)):
    """Use the patchable seam in unit tests, but query the live ConPTY in production."""
    getter = shutil.get_terminal_size
    if getter.__class__.__module__ == "unittest.mock":
        return getter(fallback)
    return terminal_size(fallback)


class TerminalPresentation:
    """Visual projection shared by the terminal app; owns no task transitions."""

    def _current_tail_geometry(self, size):
        previous = self._tail_geometry
        if previous is not None and self._drawn_size != (size.columns, size.lines):
            previous = resized_tail_geometry(previous, size.columns)
        return previous

    def _tail_clear_sequence(self):
        size = _presentation_terminal_size((100, 30))
        pending = getattr(self, "_resize_clear_geometry", None)
        if pending is not None:
            self._resize_clear_geometry = None
            return clear_live_tail(pending, terminal_height=size.lines)
        return clear_live_tail(self._current_tail_geometry(size), terminal_height=size.lines)

    def set_terminal_title(self, title: str) -> None:
        if title != self._last_terminal_title:
            self._last_terminal_title = title
            self._write(f"\x1b]0;{title}\x07")

    def update_terminal_title(self, *, running: bool | None = None) -> None:
        if running is None:
            running = bool(self._run_task and not self._run_task.done())
        if running:
            tick = self._spinner_index if not design_for(self.theme) or motion_allowed(self) else 0
            frame = _SPINNER_FRAMES[tick % len(_SPINNER_FRAMES)]
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
        # Collapse the already-rendered tool transcript after completion.  The
        # live stream remains detailed while running; this is a final-display
        # operation only and is limited to the designed terminal theme.
        collapse = getattr(self, "_collapse_completed_transcript", None)
        has_tools = any(getattr(getattr(entry, "kind", None), "value", None) == "tool" for entry in self.state.entries)
        if callable(collapse) and has_tools and self.state.status in {"completed", "accepted_partial", "failed", "error"}:
            collapse()

    def redraw(self) -> None:
        sync_attachment_input(self)
        input_text, input_cursor = self.input.display
        now = time.monotonic(); size = _presentation_terminal_size((100, 30))
        resized = self._drawn_size is not None and self._drawn_size != (size.columns, size.lines)
        # A terminal resize can reflow the old live tail before we receive the
        # next frame. Incremental cursor movement then has no reliable anchor
        # and leaves stacked input boxes behind. Repaint the visible screen
        # from durable transcript state once, then resume incremental updates.
        previous = None if resized else self._current_tail_geometry(size)
        if resized:
            # Keep the reflowed old tail available for the next append. The
            # full-screen repaint below replaces it visually, but an append
            # still needs to erase the physical rows occupied before resize.
            self._resize_clear_geometry = self._current_tail_geometry(size)
        active = bool(self._run_task and not self._run_task.done())
        palette = self.interactions.rows(self, max_rows=max(0, size.lines - 4))
        auth_view = auth_input_view(self)
        if auth_view is not None:
            input_text, input_cursor, palette = auth_view
        if not palette and self.state.plan_text and active:
            steps = [line.strip() for line in safe_text(self.state.plan_text).splitlines() if line.strip()]
            limit = max(1, min(12, size.lines - 12))
            palette = [clip_display("Task plan", size.columns)]
            completed = min(getattr(self.state, "plan_completed_steps", 0), max(0, len(steps) - 1))
            palette += [
                clip_display(
                    f"{'✓' if index < completed else '▶' if index == completed else '·'} {step}",
                    size.columns,
                )
                for index, step in enumerate(steps[:limit])
            ]
            if len(steps) > limit:
                palette.append(clip_display(f"… {len(steps) - limit} more steps in transcript", size.columns))
        self.motion.observe((self.theme, self.state.status, bool(palette)), now)
        progress = ((now % 2.4) / 2.4 if active else self.motion.progress(now)) if motion_allowed(self) else 1.0
        tick = self._spinner_index if not design_for(self.theme) or motion_allowed(self) else 0
        status, icon, status_color = status_presentation(
            self.state.status,
            self.state.execution_summary,
            self.state.active_action,
            self.catalog.language,
            self.theme,
            tick,
            self.state.task_stop_reason,
        )
        if self.state.task_budget_line and self.state.status not in {"completed", "paused", "cancelled", "error"}:
            status += " · " + clip_display(
                self.state.task_budget_line, max(24, size.columns // 3)
            )
        if self._run_task and not self._run_task.done(): status += f" [{self.submit_mode.label}]"
        if self.interactions.steering.pending_count: status += " · " + self.interactions.steering.status_line()
        frame = render_live_tail_frame(
            input_text,
            status,
            size.columns,
            cursor_index=input_cursor,
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
                tokens=self.state.context_budget.context_tokens if self.state.context_budget.context_tokens is not None else self.state.total_tokens,
                context_window=self._context_window(),
                show_percentage=self.state.context_budget.window_input_cap is not None or design_for(self.theme) is None,
                window_number=self.state.context_budget.window_number,
                task_spent=self.state.context_budget.task_tokens_spent,
                task_limit=self.state.context_budget.task_token_limit,
                task_reserved=self.state.context_budget.task_tokens_reserved,
                phase_durations=self.state.phase_durations,
                branch=self._git_branch() if design_for(self.theme) is None else None,
            ),
            previous=previous,
            theme=self.theme,
            motion_progress=progress, exiting=self.motion.exiting, active=active,
            expanded=getattr(self, "composer_expanded", True),
        )
        # Hide intermediate cursor moves; erase and replacement share one flush.
        prefix = ""
        if resized:
            transcript = render_entries(
                self.state.entries, size.columns, theme=self.theme, color=self.color,
            )
            prefix = "\x1b[2J\x1b[H" + (transcript + "\n\r" if transcript else "")
        self._write(prefix + "\x1b[?25l" + frame.text + "\x1b[?25h")
        self._tail_geometry = frame.geometry; self._redraw_dirty = False; self._drawn_draft_revision = self.state.draft_revision; self._drawn_size = (size.columns, size.lines)

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
        if self.state.context_budget.window_input_cap is not None:
            return self.state.context_budget.window_input_cap
        if self.profiles and hasattr(self.profiles, "_profiles"):
            curr = getattr(self.profiles, "_current", None)
            if curr and curr in self.profiles._profiles:
                profile = self.profiles._profiles[curr]
                return getattr(profile, "context_window", 128_000)
        return 128_000
