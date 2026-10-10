"""Bounded delivery projection; never a second authoritative task state store."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from .events import AgentEvent, EventKind

_EXECUTION = {'completed', 'accepted_partial', 'failed', 'cancelled', 'waiting_decision',
              'paused', 'interrupted', 'unknown'}
_VERIFICATION = {'verified', 'unverified', 'partial', 'unknown'}


@dataclass(frozen=True)
class TaskResult:
    execution_status: str = 'unknown'
    changes: str = 'unknown'
    verification_status: str = 'unknown'
    remaining: tuple[str, ...] = ()
    stop_code: str = 'missing_terminal'
    stop_reason: str | None = None

    def __post_init__(self):
        if self.execution_status not in _EXECUTION or self.changes not in {'changed', 'unchanged', 'unknown'}:
            raise ValueError('invalid result status')
        if self.verification_status not in _VERIFICATION:
            raise ValueError('invalid verification status')
        remaining = tuple(self.remaining)
        if len(remaining) > 32 or any(not isinstance(x, str) or len(x) > 1024 for x in remaining):
            raise ValueError('remaining must be bounded text')
        object.__setattr__(self, 'remaining', remaining)
        for value in (self.stop_code, self.stop_reason):
            if value is not None and (not isinstance(value, str) or len(value) > 1024):
                raise ValueError('result reason must be bounded text')

    def to_dict(self):
        return {'version': 1, 'execution_status': self.execution_status, 'changes': self.changes,
                'verification_status': self.verification_status, 'remaining': list(self.remaining),
                'stop_code': self.stop_code, 'stop_reason': self.stop_reason}

    @classmethod
    def from_dict(cls, data: Mapping):
        if data.get('version', 1) != 1:
            raise ValueError('unsupported task result version')
        return cls(**{key: data[key] for key in ('execution_status', 'changes',
                     'verification_status', 'remaining', 'stop_code', 'stop_reason') if key in data})

    def exit_code(self, *, require_verified: bool = False) -> int:
        if self.execution_status == 'completed':
            return 5 if require_verified and self.verification_status != 'verified' else 0
        return {'failed': 1, 'cancelled': 130, 'waiting_decision': 3, 'paused': 4,
                'interrupted': 4, 'accepted_partial': 5, 'unknown': 4}[self.execution_status]


def result_from_task(task, state=None, *, verification='unknown', remaining=(), stop_code=None) -> TaskResult:
    status = task.status.value
    execution = status if status in _EXECUTION else 'interrupted'
    changes = 'unknown' if state is None else ('changed' if state.files_changed else 'unchanged')
    return TaskResult(execution, changes, verification, tuple(remaining),
                      stop_code or (status if status in _EXECUTION else 'missing_terminal'), task.stop_reason)


class ResultCollector:
    """Legacy events prove execution only; model text never proves a terminal."""
    def __init__(self):
        self.result = TaskResult()
        self.task_id = None
        self.thread_id = None
        self.cancelled = False

    def observe(self, event: AgentEvent) -> None:
        payload = event.payload
        self.task_id = payload.get('task_id', self.task_id)
        self.thread_id = payload.get('thread_id', self.thread_id)
        if event.kind is EventKind.CANCELLED:
            self.cancelled = True
            self.result = TaskResult('cancelled', stop_code='cancelled', stop_reason=payload.get('reason'))
            return
        raw = payload.get('result')
        if isinstance(raw, Mapping) and event.kind in {
            EventKind.COMPLETED, EventKind.TASK_RESULT, EventKind.TASK_STATUS_CHANGED,
            EventKind.TASK_PAUSED, EventKind.TASK_DECISION_REQUIRED}:
            candidate = TaskResult.from_dict(raw)
            if not self.cancelled:
                self.result = candidate
            return
        if not self.cancelled:
            if event.kind is EventKind.COMPLETED:
                self.result = TaskResult('completed', verification_status='unknown', stop_code='completed')
            elif event.kind is EventKind.ERROR:
                self.result = TaskResult('failed', stop_code='execution_error')
            elif event.kind in {EventKind.TASK_STATUS_CHANGED, EventKind.TASK_PAUSED, EventKind.TASK_DECISION_REQUIRED}:
                status = payload.get('status', 'waiting_decision' if event.kind is EventKind.TASK_DECISION_REQUIRED else None)
                if status in _EXECUTION:
                    self.result = TaskResult(status, stop_code=status, stop_reason=payload.get('reason'))

    def reconcile(self, durable: TaskResult) -> TaskResult:
        """Task pause can persist a cancelled run; preserve that run's exit fact."""
        if self.cancelled:
            return TaskResult('cancelled', durable.changes, durable.verification_status,
                              durable.remaining, 'cancelled', self.result.stop_reason)
        return durable
