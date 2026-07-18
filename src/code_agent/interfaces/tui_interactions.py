from __future__ import annotations

from .command_availability import available_services
from .command_registry import REGISTRY
from .diff_view import DiffController, GitDiffSource
from .picker import PickerState, command_picker_items
from .terminal_display import DisplayKind
from .steering_view import SteeringQueueView, SteeringStage
from code_agent.core.events import EventKind
from .agent_status import AgentRunStatusProjection


class TuiInteractions:
    def __init__(self, diff_source: GitDiffSource | None = None) -> None:
        self.picker = PickerState(limit=6)
        self.diff = DiffController(diff_source)
        self.approval_choice = 0
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
        services = available_services(app)
        parent, query = _picker_context(app.input.text)
        self.picker.set_items(command_picker_items(REGISTRY.all(), services, parent=parent))
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
            if key in {"left", "up"}:
                self.approval_choice = 0
            elif key in {"right", "down"}:
                self.approval_choice = 1
            elif key.casefold() in {"y", "n"}:
                await self._resolve_approval(app, key.casefold() == "y")
            elif key in {"\x1b", "\r", "\n"}:
                await self._resolve_approval(app, self.approval_choice == 1 and key not in {"\x1b"})
            else:
                return True
            return True
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
            if _is_complete_command(app.input.text, available_services(app)):
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
        view = await self.diff.load(app.state.diff)
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


def _picker_context(text: str) -> tuple[object | None, str]:
    if not text.startswith("/"):
        return None, ""
    return None, text[1:]


def _is_complete_command(text: str, services: set[str]) -> bool:
    spec, arguments, error = REGISTRY.parse(text, services)
    if error or spec is None:
        return False
    if arguments:
        if spec.actions:
            action = REGISTRY.resolve_action(spec, arguments[0])
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
