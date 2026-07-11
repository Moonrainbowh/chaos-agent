from __future__ import annotations

import json
from typing import AsyncIterator, Optional

from ._session_io import SessionJournal
from ._tool_feedback import tool_failure
from .cancellation import CancellationError, CancellationToken
from .errors import (
    AgentEngineError,
    ContextBuildError,
    EngineLimitError,
    ModelStreamError,
)
from .events import AgentEvent, EventKind
from .limits import EngineLimits, add_usage, usage_payload
from .models import (
    ActionRequest,
    ContextBundle,
    Message,
    ModelEvent,
    ModelEventKind,
    ToolCall,
    Usage,
)
from .protocols import (
    ActionDispatcher,
    ContextBuilder,
    ModelClient,
    SessionRepository,
)


class AgentEngine:
    def __init__(
        self,
        model: ModelClient,
        context: ContextBuilder,
        actions: ActionDispatcher,
        sessions: SessionRepository,
        *,
        limits: Optional[EngineLimits] = None,
    ) -> None:
        self._model = model
        self._context = context
        self._actions = actions
        self._journal = SessionJournal(sessions)
        self._limits = limits or EngineLimits()

    async def run(
        self,
        user_input: str,
        *,
        thread_id: Optional[str] = None,
        cancellation: Optional[CancellationToken] = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run one user request and stream events after durable persistence."""
        if not isinstance(user_input, str):
            raise TypeError("user_input must be a string")
        if not user_input.strip():
            raise ValueError("user_input must not be blank")
        if thread_id is not None and (
            not isinstance(thread_id, str) or not thread_id.strip()
        ):
            raise ValueError("thread_id must be a non-blank string or None")

        token = cancellation or CancellationToken()
        active_thread = thread_id or await self._journal.create_thread()
        started = AgentEvent(
            kind=EventKind.RUN_STARTED,
            payload={"thread_id": active_thread},
        )
        await self._journal.append_event(active_thread, started)
        yield started

        try:
            token.raise_if_cancelled()
            prior_messages = await self._journal.load_messages(active_thread)
            user_message = Message(role="user", content=user_input)
            await self._journal.append_message(active_thread, user_message)
            added = self._journal.message_added(user_message)
            await self._journal.append_event(active_thread, added)
            yield added

            messages = prior_messages + (user_message,)
            used_call_ids: set[str] = set()
            tool_call_count = 0
            total_usage = Usage()

            for turn in range(1, self._limits.max_model_turns + 1):
                token.raise_if_cancelled()
                turn_started = AgentEvent(
                    kind=EventKind.TURN_STARTED,
                    payload={"turn": turn},
                )
                await self._journal.append_event(active_thread, turn_started)
                yield turn_started

                source_messages = prior_messages if turn == 1 else messages
                source_input = user_input if turn == 1 else ""
                try:
                    bundle = await self._context.build(source_messages, source_input)
                    if not isinstance(bundle, ContextBundle):
                        raise TypeError("context builder returned an invalid bundle")
                except CancellationError:
                    raise
                except Exception:
                    raise ContextBuildError("context build failed") from None

                built = AgentEvent(
                    kind=EventKind.CONTEXT_BUILT,
                    payload={"turn": turn, "message_count": len(bundle.messages)},
                )
                await self._journal.append_event(active_thread, built)
                yield built

                model_started = AgentEvent(
                    kind=EventKind.MODEL_STARTED,
                    payload={"turn": turn},
                )
                await self._journal.append_event(active_thread, model_started)
                yield model_started

                text_parts: list[str] = []
                calls: list[ToolCall] = []
                completed = False
                try:
                    tools = tuple(self._actions.tools())
                    tool_names = {tool.name for tool in tools}
                    if len(tool_names) != len(tools):
                        raise ModelStreamError("action dispatcher exposed duplicate tools")
                    stream = self._model.stream(
                        bundle.system_prompt,
                        bundle.messages,
                        tools,
                    )
                    async for model_event in stream:
                        token.raise_if_cancelled()
                        if completed:
                            raise ModelStreamError(
                                "model emitted an event after completion"
                            )
                        self._accumulate_model_event(
                            model_event,
                            text_parts,
                            calls,
                        )
                        if model_event.kind is ModelEventKind.COMPLETED:
                            completed = True
                        streamed = AgentEvent(
                            kind=EventKind.MODEL_EVENT,
                            payload={"event": model_event.to_dict()},
                        )
                        await self._journal.append_event(active_thread, streamed)
                        yield streamed
                        if model_event.usage is not None:
                            total_usage = add_usage(total_usage, model_event.usage)
                            if total_usage.total_tokens > self._limits.max_total_tokens:
                                raise EngineLimitError("token budget exceeded")
                except (AgentEngineError, CancellationError):
                    raise
                except Exception:
                    raise ModelStreamError("model stream failed") from None

                if not completed:
                    raise ModelStreamError("model stream ended before completion")

                assistant = Message(
                    role="assistant",
                    content="".join(text_parts),
                    tool_calls=tuple(calls),
                )
                await self._journal.append_message(active_thread, assistant)
                messages += (assistant,)
                assistant_added = self._journal.message_added(assistant)
                await self._journal.append_event(active_thread, assistant_added)
                yield assistant_added

                if not calls:
                    finished = AgentEvent(
                        kind=EventKind.COMPLETED,
                        payload={
                            "thread_id": active_thread,
                            "turns": turn,
                            "tool_calls": tool_call_count,
                            "usage": usage_payload(total_usage),
                        },
                    )
                    await self._journal.append_event(active_thread, finished)
                    yield finished
                    return

                if turn >= self._limits.max_model_turns:
                    raise EngineLimitError("model turn budget exceeded")
                if tool_call_count + len(calls) > self._limits.max_tool_calls:
                    raise EngineLimitError("tool call budget exceeded")
                if len({call.id for call in calls}) != len(calls) or any(
                    call.id in used_call_ids for call in calls
                ):
                    raise ModelStreamError("model reused a tool call id")

                for call in calls:
                    used_call_ids.add(call.id)
                    tool_call_count += 1
                    async for action_event in self._dispatch(
                        active_thread,
                        call,
                        token,
                        is_available=call.name in tool_names,
                    ):
                        if action_event.kind is EventKind.MESSAGE_ADDED:
                            result_message = Message.from_dict(
                                action_event.payload["message"]  # type: ignore[arg-type]
                            )
                            messages += (result_message,)
                        yield action_event

            raise EngineLimitError("model turn budget exceeded")
        except CancellationError as exc:
            cancelled = AgentEvent(
                kind=EventKind.CANCELLED,
                payload={"reason": exc.reason},
            )
            await self._journal.append_event(active_thread, cancelled)
            yield cancelled
        except AgentEngineError as exc:
            failed = AgentEvent(
                kind=EventKind.ERROR,
                payload={"code": exc.code, "error_type": type(exc).__name__},
            )
            await self._journal.append_event(active_thread, failed)
            yield failed
            raise

    async def _dispatch(
        self,
        thread_id: str,
        call: ToolCall,
        token: CancellationToken,
        *,
        is_available: bool,
    ) -> AsyncIterator[AgentEvent]:
        request = ActionRequest(id=call.id, name=call.name, arguments=call.arguments)
        requested = AgentEvent(
            kind=EventKind.ACTION_REQUESTED,
            payload={"request": request.to_dict()},
        )
        await self._journal.append_event(thread_id, requested)
        yield requested
        if not is_available:
            result = tool_failure(call, "tool is not available")
        else:
            started = AgentEvent(
                kind=EventKind.ACTION_STARTED,
                payload={"request_id": call.id, "name": call.name},
            )
            await self._journal.append_event(thread_id, started)
            yield started
            try:
                result = await self._actions.dispatch(request, token)
                if result.request_id != call.id or result.name != call.name:
                    result = tool_failure(call, "invalid tool result")
            except CancellationError:
                raise
            except Exception as exc:
                result = tool_failure(
                    call,
                    "tool execution failed",
                    type(exc).__name__,
                )

        completed = AgentEvent(
            kind=EventKind.ACTION_COMPLETED,
            payload={"result": result.to_dict()},
        )
        await self._journal.append_event(thread_id, completed)
        yield completed

        content = json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        message = Message(
            role="tool",
            name=call.name,
            tool_call_id=call.id,
            content=content,
        )
        await self._journal.append_message(thread_id, message)
        added = self._journal.message_added(message)
        await self._journal.append_event(thread_id, added)
        yield added

    def _accumulate_model_event(
        self,
        event: ModelEvent,
        text_parts: list[str],
        calls: list[ToolCall],
    ) -> None:
        if not isinstance(event, ModelEvent):
            raise ModelStreamError("model emitted an invalid event")
        if event.kind is ModelEventKind.TEXT_DELTA:
            text_parts.append(event.text or "")
            if sum(map(len, text_parts)) > self._limits.max_assistant_chars:
                raise EngineLimitError("assistant output budget exceeded")
        elif event.kind is ModelEventKind.TOOL_CALL:
            if event.tool_call is None:
                raise ModelStreamError("tool call event has no call")
            calls.append(event.tool_call)
