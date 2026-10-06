from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from code_agent.core.attachments import AttachmentRef
from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.task_result import ResultCollector, TaskResult
from .controller import AgentController
from .terminal_renderer import ColorMode, Theme, render_entries
from .terminal_state import TerminalState
from .windows_tui import WindowsTerminalApp
from .task_controller import ForegroundTaskController


class CommandKind(str, Enum):
    TUI = "tui"
    ASK = "ask"
    RESUME = "resume"
    RUN_JSON = "run_json"
    TASK_LIST = "task_list"
    TASK_RESUME = "task_resume"
    TASK_RECOVERY = "task_recovery"
    TASK_RESOLVE = "task_resolve"
    TASK_RESULT = "task_result"


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    prompt: Optional[str] = None
    thread_id: Optional[str] = None
    recovery_decision: dict[str, object] | None = None
    require_verified: bool = False


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
        required = values[2] == '--require-verified'
        return Command(CommandKind.RUN_JSON, prompt=_joined(values[3:] if required else values[2:], "run"), require_verified=required)
    if values[0] == "task":
        if len(values) == 3 and values[1] == 'result' and values[2].strip():
            return Command(CommandKind.TASK_RESULT, thread_id=values[2])
        if len(values) == 3 and values[1] == "recovery" and values[2].strip():
            return Command(CommandKind.TASK_RECOVERY, thread_id=values[2])
        if len(values) == 9 and values[1] == "resolve":
            if not all(value.strip() for value in values[2:]):
                raise ValueError("recovery fields must not be blank")
            sequence = int(values[4])
            if sequence <= 0 or values[6] not in {'durable_receipt', 'local_mutation', 'operator_executed', 'operator_not_executed'}:
                raise ValueError("invalid recovery sequence or decision")
            return Command(CommandKind.TASK_RESOLVE, thread_id=values[2], recovery_decision={
                'call_id': values[3], 'message_sequence': sequence, 'version': values[5],
                'decision': values[6], 'reason': values[7], 'evidence': values[8]})
        if len(values) == 2 and values[1] == "list":
            return Command(CommandKind.TASK_LIST)
        if len(values) >= 3 and values[1] == "resume":
            return Command(CommandKind.TASK_RESUME, thread_id=values[2], prompt=" ".join(values[3:]) or "continue safely")
        raise ValueError("task requires list, resume, recovery, or resolve")
    raise ValueError(f"unknown command: {values[0]}")


async def execute_command(
    command: Command,
    controller: AgentController,
    tui: WindowsTerminalApp,
    write: Callable[[str], object],
    tasks: ForegroundTaskController | None = None,
    *,
    attachments: Sequence[AttachmentRef] = (),
) -> int:
    """Execute parsed command behavior using dependencies supplied at integration."""
    if command.kind is CommandKind.TUI:
        await tui.run(thread_id=command.thread_id)
        return 0
    if command.kind is CommandKind.TASK_RESULT:
        if tasks is None:
            raise ValueError('task controls are unavailable')
        result = await tasks.result(command.thread_id)
        write(json.dumps(result.to_dict(), ensure_ascii=False) + '\n')
        return 0
    if command.kind in {CommandKind.TASK_RECOVERY, CommandKind.TASK_RESOLVE}:
        if tasks is None:
            raise ValueError("task controls are unavailable")
        if command.kind is CommandKind.TASK_RECOVERY:
            data = await tasks.recovery_checklist(command.thread_id or '')
        else:
            if command.recovery_decision is None:
                raise ValueError("missing explicit recovery decision")
            event = await tasks.resolve_pending_action(command.thread_id or '',
                **command.recovery_decision, operator_authorized=True)
            data = event.to_dict()
        write(json.dumps(data, ensure_ascii=False, sort_keys=True) + '\n')
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
        collected = await _write_rendered_events(
            tasks.resume(
                command.thread_id or "",
                _required_prompt(command),
                attachments=attachments,
            ),
            write,
        )
        return await _finish_result(collected, tasks, command.thread_id, write)
    if command.kind in {CommandKind.ASK, CommandKind.RUN_JSON}:
        if tasks is None:
            raise ValueError("task controls are unavailable")
        task = await tasks.start(_required_prompt(command))
        events = tasks.events(
            task.id, _required_prompt(command), attachments=attachments
        )
        if command.kind is CommandKind.RUN_JSON:
            collected = await _write_json_events(events, write)
        else:
            collected = await _write_rendered_events(events, write)
        return await _finish_result(collected, tasks, task.id, write,
                                    json_output=command.kind is CommandKind.RUN_JSON,
                                    require_verified=command.require_verified)
    if command.kind is CommandKind.RESUME:
        resume_thread = getattr(tasks, "resume_thread", None) if tasks else None
        events = (
            resume_thread(
                command.thread_id or "",
                _required_prompt(command),
                attachments=attachments,
            )
            if callable(resume_thread)
            else controller.resume(
                command.thread_id or "",
                _required_prompt(command),
                attachments=attachments,
            )
        )
    else:
        raise AssertionError(f"unhandled command kind: {command.kind}")
    collected = await _write_rendered_events(events, write)
    return await _finish_result(collected, tasks, collected.task_id, write)


def _joined(values: Sequence[str], command: str) -> str:
    prompt = " ".join(values)
    if not prompt.strip():
        raise ValueError(f"{command} requires a non-blank prompt")
    return prompt


def _required_prompt(command: Command) -> str:
    if command.prompt is None:
        raise ValueError("command has no prompt")
    return command.prompt


async def _write_rendered_events(
    events: AsyncIterator[AgentEvent], write: Callable[[str], object]
) -> ResultCollector:
    state = TerminalState()
    collected = ResultCollector()
    try:
        async for event in events:
            collected.observe(event)
            state.apply(event)
    except Exception:
        collected.result = TaskResult('failed', stop_code='execution_error')
        state.apply(AgentEvent(EventKind.ERROR, {'code': 'execution_error'}))
    finally:
        rendered = render_entries(
            state.entries,
            shutil.get_terminal_size((100, 30)).columns,
            theme=Theme.SYMBOL,
            color=ColorMode.AUTO,
        )
        if rendered:
            write(rendered + "\n")
    return collected


async def _write_json_events(
    events: AsyncIterator[AgentEvent], write: Callable[[str], object]
) -> ResultCollector:
    collected = ResultCollector()
    try:
        async for event in events:
            collected.observe(event)
            write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True,
                separators=(',', ':')) + '\n')
    except Exception:
        collected.result = TaskResult('failed', stop_code='execution_error')
        write(json.dumps(AgentEvent(EventKind.ERROR, {'code': 'execution_error'}).to_dict()) + '\n')
    return collected


async def _finish_result(collected, tasks, task_id, write, *, json_output=False, require_verified=False):
    result = collected.result
    load = getattr(tasks, 'result', None)
    if task_id and callable(load):
        try:
            result = await load(task_id)
            if not isinstance(result, TaskResult):
                raise TypeError('invalid durable result')
            result = collected.reconcile(result)
        except Exception:
            result = TaskResult(stop_code='state_read_failed')
    if json_output:
        event = AgentEvent(EventKind.TASK_RESULT, {'task_id': task_id, 'result': result.to_dict()})
        write(json.dumps(event.to_dict(), ensure_ascii=False, separators=(',', ':')) + '\n')
    else:
        write(f'Result: {result.execution_status}; changes={result.changes}; verification={result.verification_status}\n')
    return result.exit_code(require_verified=require_verified)
