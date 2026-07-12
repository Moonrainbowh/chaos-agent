from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ModelEvent, ModelEventKind

from .controller import AgentController
from .windows_tui import WindowsTerminalApp
from .task_controller import ForegroundTaskController


class CommandKind(str, Enum):
    TUI = "tui"
    ASK = "ask"
    RESUME = "resume"
    RUN_JSON = "run_json"
    TASK_LIST = "task_list"
    TASK_RESUME = "task_resume"


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    prompt: Optional[str] = None
    thread_id: Optional[str] = None


def parse_command(arguments: Sequence[str]) -> Command:
    """Parse the package command grammar without printing or exiting."""
    values = tuple(arguments)
    if not all(isinstance(value, str) for value in values):
        raise TypeError("command arguments must be text")
    if not values:
        return Command(CommandKind.TUI)
    if values[0] == "ask":
        return Command(CommandKind.ASK, prompt=_joined(values[1:], "ask"))
    if values[0] == "resume":
        if len(values) < 2 or not values[1].strip():
            raise ValueError("resume requires a thread id")
        if len(values) == 2:
            return Command(CommandKind.TUI, thread_id=values[1])
        return Command(
            CommandKind.RESUME,
            prompt=_joined(values[2:], "resume"),
            thread_id=values[1],
        )
    if values[0] == "run":
        if len(values) < 3 or values[1] != "--json":
            raise ValueError("run requires --json followed by a prompt")
        return Command(CommandKind.RUN_JSON, prompt=_joined(values[2:], "run"))
    if values[0] == "task":
        if len(values) == 2 and values[1] == "list":
            return Command(CommandKind.TASK_LIST)
        if len(values) >= 3 and values[1] == "resume":
            return Command(CommandKind.TASK_RESUME, thread_id=values[2], prompt=" ".join(values[3:]) or "continue safely")
        raise ValueError("task requires list or resume <task-id>")
    raise ValueError(f"unknown command: {values[0]}")


async def execute_command(
    command: Command,
    controller: AgentController,
    tui: WindowsTerminalApp,
    write: Callable[[str], object],
    tasks: ForegroundTaskController | None = None,
) -> int:
    """Execute parsed command behavior using dependencies supplied at integration."""
    if command.kind is CommandKind.TUI:
        await tui.run(thread_id=command.thread_id)
        return 0
    if command.kind is CommandKind.TASK_LIST:
        if tasks is None:
            raise ValueError("task controls are unavailable")
        for task in await tasks.list(include_terminal=True):
            write(f"{task.id} {task.status.value} {task.contract.objective[:120]}\n")
        return 0
    if command.kind is CommandKind.TASK_RESUME:
        if tasks is None:
            raise ValueError("task controls are unavailable")
        async for event in tasks.resume(command.thread_id or "", _required_prompt(command)):
            line = _plain_event(event)
            if line:
                write(line)
        return 0
    if command.kind is CommandKind.RUN_JSON:
        async for line in controller.run_json(_required_prompt(command)):
            write(line + "\n")
        return 0
    events = (
        controller.resume(command.thread_id or "", _required_prompt(command))
        if command.kind is CommandKind.RESUME
        else controller.ask(_required_prompt(command))
    )
    async for event in events:
        line = _plain_event(event)
        if line:
            write(line)
    return 0


def _joined(values: Sequence[str], command: str) -> str:
    prompt = " ".join(values)
    if not prompt.strip():
        raise ValueError(f"{command} requires a non-blank prompt")
    return prompt


def _required_prompt(command: Command) -> str:
    if command.prompt is None:
        raise ValueError("command has no prompt")
    return command.prompt


def _plain_event(event: AgentEvent) -> str:
    if event.kind is EventKind.MODEL_EVENT:
        raw = event.payload.get("event")
        if isinstance(raw, Mapping):
            model_event = ModelEvent.from_dict(raw)
            if model_event.kind is ModelEventKind.TEXT_DELTA:
                return model_event.text or ""
    if event.kind is EventKind.ERROR:
        return "\nerror: " + str(event.payload.get("code", "agent error")) + "\n"
    if event.kind is EventKind.CANCELLED:
        return "\ncancelled\n"
    if event.kind is EventKind.COMPLETED:
        return "\n"
    return ""
