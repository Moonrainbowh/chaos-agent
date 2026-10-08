"""Collect advisory child output without inventing execution success."""
from __future__ import annotations

import time
import asyncio
from dataclasses import replace
from collections.abc import Mapping
from code_agent.core.cancellation import CancellationError
from code_agent.core.events import EventKind
from code_agent.core.models import Message, ModelEvent, ModelEventKind
from code_agent.core.task_result import ResultCollector, TaskResult
from code_agent.orchestration.models import AgentReference, AgentUsage, ChildRunResult, RunStatus


async def settled_child_close(closer):
    """Do not let cancellation interrupt an already-started asynchronous closer."""
    close = getattr(closer, 'aclose', None)
    if close is None:
        return False
    closing = asyncio.create_task(close())
    cancelled = False
    while not closing.done():
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            cancelled = True
    closing.result()
    return cancelled


async def settled_child_result(stream, request, cancellation, *, sessions=None):
    """Keep threaded actions and process cleanup alive until their actual completion."""
    execution = asyncio.create_task(collect_child_result(stream, request, sessions=sessions))
    try:
        return await asyncio.shield(execution)
    except asyncio.CancelledError:
        cancellation.cancel("child execution cancelled")
        while not execution.done():
            try:
                await asyncio.shield(execution)
            except asyncio.CancelledError:
                continue
        result = execution.result()
        return replace(result, status=RunStatus.CANCELLED, result=None,
            error=cancellation.reason)


async def collect_child_result(stream, request, *, sessions=None):
    started = time.monotonic()
    collected = ResultCollector()
    answers, references = [], []
    tokens = tool_calls = request_tokens = 0
    try:
        async for event in stream:
            collected.observe(event)
            if event.kind is EventKind.RUN_STARTED:
                thread = event.payload.get('thread_id')
                if isinstance(thread, str):
                    references.append(AgentReference('thread', thread))
            elif event.kind is EventKind.MODEL_STARTED:
                tokens += request_tokens
                request_tokens = 0
            elif event.kind is EventKind.ACTION_REQUESTED:
                tool_calls += 1
            elif event.kind is EventKind.MESSAGE_ADDED:
                raw = event.payload.get('message')
                if isinstance(raw, Mapping):
                    message = Message.from_dict(raw)
                    if message.role == 'assistant' and message.content:
                        answers.append(message.content)
            elif event.kind is EventKind.MODEL_EVENT:
                raw = event.payload.get('event')
                if isinstance(raw, Mapping):
                    model = ModelEvent.from_dict(raw)
                    if model.kind is ModelEventKind.USAGE and model.usage:
                        # Providers emit cumulative snapshots for one request.
                        request_tokens = model.usage.total_tokens
    except CancellationError as error:
        if not collected.cancelled:
            collected.result = TaskResult('cancelled', stop_code='cancelled', stop_reason=error.reason)
    except Exception:
        if not collected.cancelled:
            collected.result = TaskResult('failed', stop_code='execution_error')
    tokens += request_tokens
    result = collected.result
    usage_complete = False
    if sessions is not None and collected.thread_id:
        try:
            records = await sessions.context_records(collected.thread_id, 'usage')
            usage_complete = bool(records) and all(record.get('status') == 'settled' for record in records)
            actual = sum(record.get('usage', {}).get('input_tokens', 0)
                + record.get('usage', {}).get('output_tokens', 0) for record in records)
            if records:
                tokens = actual
        except Exception:
            pass  # Event observations remain a lower bound if the journal cannot be read.
    if result.execution_status == 'unknown' and sessions is not None and collected.thread_id:
        try:
            task = await sessions.load_task_for_thread(collected.thread_id)
            if task is not None:
                from code_agent.core.task_result import result_from_task
                result = result_from_task(task, await sessions.load_task_state(task.thread_id))
        except Exception:
            result = TaskResult(stop_code='state_read_failed')
    status = {'completed': RunStatus.COMPLETED, 'failed': RunStatus.FAILED,
              'cancelled': RunStatus.CANCELLED, 'waiting_decision': RunStatus.WAITING_DECISION,
              'paused': RunStatus.PAUSED, 'accepted_partial': RunStatus.WAITING_DECISION}.get(
                  result.execution_status, RunStatus.INTERRUPTED)
    summary = '\n\n'.join(answers).strip()[:16_384]
    if not summary and status is RunStatus.COMPLETED:
        summary = 'Child execution completed without an advisory message; verification is ' + result.verification_status + '.'
    return ChildRunResult(request.run_id, status, summary,
        AgentUsage(tokens, tool_calls, int(time.monotonic() - started)), tuple(references),
        error=None if status is RunStatus.COMPLETED else result.stop_code, result=result,
        usage_complete=usage_complete)
