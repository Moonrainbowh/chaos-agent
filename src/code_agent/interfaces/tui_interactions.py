from __future__ import annotations

from .command_availability import available_services
from .command_registry import REGISTRY
from .diff_view import DiffController, GitDiffSource
from .picker import (
    PickerState,
    command_picker_items,
    mcp_picker_items,
    skill_picker_items,
)
from .terminal_display import DisplayKind
from .steering_view import SteeringQueueView, SteeringStage
from code_agent.core.events import EventKind
from .agent_status import AgentRunStatusProjection
from .interaction import (
    InteractionPrimitive,
    InteractionResult,
    render_interaction,
)
from .checkpoint_tui import handle_rewind_key, rewind_rows


class TuiInteractions:
    def __init__(self, diff_source: GitDiffSource | None = None) -> None:
        self.picker = PickerState(limit=6)
        self.diff = DiffController(diff_source)
        self.approval_choice = 0
        self.interaction_choice = 0
        self.steering = SteeringQueueView()
        self.agent_status = AgentRunStatusProjection()

    def rows(self, app: object) -> tuple[str, ...]:
        approval = app._pending_approval
        if approval is not None:
            target = approval.target or approval.name
            risk = f" · risk {approval.risk}" if approval.risk else ""
            rows = [f"approval · {approval.name}{risk}", f"target: {target}"]
            rows.extend(
                (
                    ("› " if self.approval_choice == 0 else "  ") + "No",
                    ("› " if self.approval_choice == 1 else "  ") + "Yes",
                    "Enter select · Esc cancel",
                )
            )
            return tuple(rows)
        interaction = getattr(app, "_pending_interaction", None)
        if interaction is not None:
            return render_interaction(interaction, self.interaction_choice)
        rewind = rewind_rows(app)
        if rewind is not None:
            return rewind
        services = available_services(app)
        dynamic = _dynamic_items(app)
        if dynamic is not None:
            items, query = dynamic
            self.picker.set_items(items)
            self.picker.update_query(query)
            return self.picker.rows(app._columns())
        parent, query = _picker_context(app.input.text)
        registry = getattr(app, "command_registry", REGISTRY)
        self.picker.set_items(command_picker_items(registry.all(), services, parent=parent))
        self.picker.update_query(query)
        return self.picker.rows(app._columns()) if app.input.text.startswith("/") else ()

    async def steer(self, app: object, task_id: str, instruction: str) -> None:
        item = self.steering.queue(instruction)
        app._append(DisplayKind.METADATA, f"queued · queue {self.steering.pending_count}")
        await app.tasks.steer(task_id, instruction)
        self.steering.transition(item.identifier, SteeringStage.STEERED)
        app._append(DisplayKind.METADATA, f"steered · queue {self.steering.pending_count}")

    def observe_event(self, app: object, kind: EventKind) -> None:
        target = None
        if kind is EventKind.TURN_STARTED:
            target = SteeringStage.DEQUEUED
        elif kind is EventKind.CONTEXT_BUILT:
            target = SteeringStage.APPLIED
        if target is None:
            return
        changed = False
        for item in self.steering.items:
            if target is SteeringStage.DEQUEUED and item.stage is SteeringStage.STEERED:
                self.steering.transition(item.identifier, target)
                changed = True
            elif target is SteeringStage.APPLIED and item.stage is SteeringStage.DEQUEUED:
                self.steering.transition(item.identifier, target)
                changed = True
        if changed:
            app._append(DisplayKind.METADATA, self.steering.status_line())

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
        return await self._handle_picker_key(app, key)

    async def _handle_approval_key(self, app: object, key: str) -> bool:
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
        if not app.input.text.startswith("/"):
            return False
        self.rows(app)
        if key == "up":
            self.picker.move(-1)
            return True
        if key == "down":
            self.picker.move(1)
            return True
        if key == "\x1b":
            app.input.clear()
            return True
        if key == "\r":
            if _is_complete_command(
                app.input.text,
                available_services(app),
                getattr(app, "command_registry", REGISTRY),
            ):
                await app.submit(app.input.submit())
                return True
            selection = self.picker.accept()
            if selection is None:
                await app.submit(app.input.submit())
                return True
            app.input.replace(selection.completion)
            if selection.completion.endswith(" "):
                return True
            await app.submit(app.input.submit())
            return True
        return False

    async def show_diff(self, app: object) -> None:
        view = await self.diff.load(DiffScope.WORKING_TREE, app.state.diff)
        for entry in view.render():
            app.state.entries.append(entry)
            app.state.transcript.append(entry.text)
        app._flush_pending_entries()

    async def _resolve_approval(self, app: object, approved: bool) -> None:
        request = app._pending_approval
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


def _picker_context(text: str) -> tuple[object | None, str]:
    if not text.startswith("/"):
        return None, ""
    return None, text[1:]


def _dynamic_items(
    app: object,
) -> tuple[tuple[object, ...], str] | None:
    text = app.input.text
    parts = text.split(" ")
    if len(parts) < 3:
        return None
    command, action = parts[0].casefold(), parts[1].casefold()
    query = " ".join(parts[2:])
    if command in {"/技能", "/skill", "/skills"} and action in {
        "信息",
        "info",
        "启用",
        "enable",
        "禁用",
        "disable",
        "来源",
        "source",
    }:
        return skill_picker_items(app.skills, parts[1]), query
    if command == "/mcp" and action in {
        "status",
        "状态",
        "tools",
        "工具",
        "enable",
        "启用",
        "disable",
        "禁用",
        "restart",
        "重启",
        "diagnose",
        "诊断",
    }:
        return mcp_picker_items(app.mcp, parts[1]), query
    return None


def _is_complete_command(
    text: str, services: set[str], registry: object = REGISTRY
) -> bool:
    spec, arguments, error = registry.parse(text, services)
    if error or spec is None:
        return False
    if arguments:
        if spec.actions:
            action = registry.resolve_action(spec, arguments[0])
            if (
                action is not None
                and len(arguments) == 1
                and action.usage
                and not text[-1].isspace()
            ):
                return False
        return True
    if text[-1].isspace() and not spec.actions:
        return True
    return spec.usage.startswith("[")
