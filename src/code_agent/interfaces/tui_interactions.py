from __future__ import annotations

from .approval_card import approval_card_rows
from .diff_interaction import DiffInteraction
from .diff_view import DiffController, GitDiffSource
from .tui_diff_commands import handle_diff_key, show_diff
from .picker import PickerState
from .command_navigation import command_rows, handle_command_key
from .terminal_display import DisplayKind
from .steering_view import SteeringQueueView
from .tui_active_input import observe_active_input
from code_agent.core.events import EventKind
from .agent_status import AgentRunStatusProjection
from .interaction import (
    InteractionPrimitive,
    InteractionResult,
    render_interaction,
)
from .checkpoint_tui import handle_rewind_key, rewind_rows
from .edit_plan_approval_tui import (
    close_edit_plan_approval_diff,
    edit_plan_approval_rows,
    handle_edit_plan_approval_key,
)


class TuiInteractions:
    def __init__(self, diff_source: GitDiffSource | None = None) -> None:
        self.picker = PickerState(limit=6)
        self.diff = DiffController(diff_source)
        self.diff_interaction = DiffInteraction()
        self.approval_diff = DiffInteraction()
        self.approval_choice = 0
        self.interaction_choice = 0
        self.steering = SteeringQueueView()
        self.agent_status = AgentRunStatusProjection()

    def rows(self, app: object, *, max_rows: int = 14) -> tuple[str, ...]:
        approval = app._pending_approval
        if approval is not None:
            plan_rows = edit_plan_approval_rows(
                approval,
                self.approval_choice,
                self.approval_diff,
                columns=app._columns(),
                max_rows=max_rows,
            )
            if plan_rows is not None:
                return plan_rows
            return approval_card_rows(approval, self.approval_choice)
        interaction = getattr(app, "_pending_interaction", None)
        if interaction is not None:
            return render_interaction(interaction, self.interaction_choice)
        rewind = rewind_rows(app)
        if rewind is not None:
            return rewind
        if self.diff_interaction.active:
            return self.diff_interaction.rows(app._columns(), max_rows=max_rows)
        return command_rows(self, app)

    def observe_event(self, app: object, event: object) -> None:
        observe_active_input(self, app, event)

    def observe_agent(self, app: object, view: object) -> None:
        line = self.agent_status.observe(view)
        if line is not None:
            app._append(DisplayKind.METADATA, line)

    async def handle_key(self, app: object, key: str) -> bool:
        if app._pending_approval is not None:
            return await self._handle_approval_key(app, key)
        interaction = getattr(app, "_pending_interaction", None)
        if interaction is not None:
            return await self._handle_interaction_key(app, interaction, key)
        if await handle_rewind_key(app, key):
            return True
        if self.diff_interaction.active:
            return await self._handle_diff_key(app, key)
        return await self._handle_picker_key(app, key)

    async def _handle_approval_key(self, app: object, key: str) -> bool:
        if handle_edit_plan_approval_key(
            app._pending_approval, self.approval_diff, key
        ):
            return True
        if key in {"left", "up"}:
            self.approval_choice = 0
        elif key in {"right", "down"}:
            self.approval_choice = 1
        elif key.casefold() in {"y", "n"}:
            await self._resolve_approval(app, key.casefold() == "y")
        elif key in {"\x1b", "\r", "\n"}:
            approved = self.approval_choice == 1 and key != "\x1b"
            await self._resolve_approval(app, approved)
        return True

    async def _handle_interaction_key(
        self, app: object, interaction: object, key: str
    ) -> bool:
        if interaction.primitive is InteractionPrimitive.INPUT:
            if key == "\x1b":
                await self._resolve_interaction(app, False, None, True)
            elif key == "\r":
                value = app.input.submit()
                await self._resolve_interaction(app, bool(value.strip()), value or None)
            else:
                return False
            return True
        options = (
            ("No", "Yes")
            if interaction.primitive is InteractionPrimitive.CONFIRM
            else interaction.options
        )
        if key in {"left", "up"}:
            self.interaction_choice = max(0, self.interaction_choice - 1)
        elif key in {"right", "down"}:
            self.interaction_choice = min(len(options) - 1, self.interaction_choice + 1)
        elif key == "\x1b":
            await self._resolve_interaction(app, False, None, True)
        elif key in {"\r", "\n"}:
            accepted = (
                self.interaction_choice == 1
                if interaction.primitive is InteractionPrimitive.CONFIRM
                else True
            )
            await self._resolve_interaction(
                app, accepted, options[self.interaction_choice]
            )
        return True

    async def _handle_picker_key(self, app: object, key: str) -> bool:
        return await handle_command_key(self, app, key)

    async def show_diff(self, app: object) -> None:
        await show_diff(self, app)

    async def _handle_diff_key(self, app: object, key: str) -> bool:
        return await handle_diff_key(self, app, key)

    async def _resolve_approval(self, app: object, approved: bool) -> None:
        request = app._pending_approval
        close_edit_plan_approval_diff(self.approval_diff)
        app.approvals.resolve(request.request_id, approved)
        app._pending_approval = None
        app._approval_done.set()
        self.approval_choice = 0

    async def _resolve_interaction(
        self,
        app: object,
        accepted: bool,
        value: str | None,
        cancelled: bool = False,
    ) -> None:
        request = app._pending_interaction
        app.interaction_broker.resolve(
            InteractionResult(request.identifier, accepted, value, cancelled)
        )
        app._pending_interaction = None
        app._interaction_done.set()
        self.interaction_choice = 0
