from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from .terminal_motion import motion_allowed
from .terminal_theme import design_for
from .terminal_tail import render_live_tail_frame
from .terminal_status import status_presentation, status_context

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class TerminalPresentation:
    """Visual projection shared by the terminal app; owns no task transitions."""

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

    def redraw(self) -> None:
        now = time.monotonic(); size = shutil.get_terminal_size((100, 30))
        active = bool(self._run_task and not self._run_task.done())
        palette = self.interactions.rows(self, max_rows=max(0, size.lines - 4))
        self.motion.observe((self.theme, self.state.status, bool(palette)), now)
        progress = self.motion.progress(now) if motion_allowed(self) else 1.0
        tick = self._spinner_index if not design_for(self.theme) or motion_allowed(self) else 0
        status, icon, status_color = status_presentation(self.state.status, self.state.execution_summary, self.state.active_action, self.catalog.language, self.theme, tick)
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
                show_percentage=design_for(self.theme) is None,
                branch=self._git_branch() if design_for(self.theme) is None else None,
            ),
            previous=self._tail_geometry,
            theme=self.theme,
            motion_progress=progress, exiting=self.motion.exiting, active=active,
        )
        self._write(frame.text)
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
        if self.profiles and hasattr(self.profiles, "_profiles"):
            curr = getattr(self.profiles, "_current", None)
            if curr and curr in self.profiles._profiles:
                profile = self.profiles._profiles[curr]
                return getattr(profile, "context_window", 128_000)
        return 128_000
