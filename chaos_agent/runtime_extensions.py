from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar, Token
from dataclasses import replace
from pathlib import Path

from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.models import Message, ModelEventKind, ToolDefinition, Usage
from code_agent.core.task_state import TaskState
from code_agent.context.tokens import estimate_tokens
from code_agent.interfaces.approval import ApprovalBroker, ApprovalRequest
from code_agent.skills.controller import SkillController
from code_agent.thread_intelligence.deterministic_summary import (
    render_bounded_source_summary,
)
from code_agent.thread_intelligence.models import (
    SummaryRequest,
    SummaryResponse,
)


_SUMMARY_SYSTEM = (
    "You create compact semantic checkpoints for an ongoing coding task."
)
_SUMMARY_INSTRUCTION = (
    "Summarize the following task history faithfully. Preserve decisions, "
    "constraints, unresolved work, evidence references, and stable source "
    "IDs. Do not add facts.\n\n"
)
_SUMMARY_PROTOCOL_RESERVE = 32
_INTERACTION_PROMPTS = {
    "ask": (
        "Interaction mode: ask. Answer or explain using read-only context. "
        "Do not modify files or run local commands."
    ),
    "code": (
        "Interaction mode: code. Implement and verify the request when asked; "
        "all tools remain subject to the task authorization and permission policy."
    ),
    "plan": (
        "Interaction mode: plan. Inspect read-only context and produce a concrete plan. "
        "Do not modify files or run local commands."
    ),
}


class ThreadRuntimeBinding:
    """Keep the Host-owned caller thread scoped to one asyncio execution tree."""

    def __init__(self) -> None:
        self._thread_id: ContextVar[str | None] = ContextVar(
            "active_thread_id", default=None
        )

    def bind(self, thread_id: str) -> Token[str | None]:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be non-blank")
        return self._thread_id.set(thread_id)

    def reset(self, token: Token[str | None]) -> None:
        self._thread_id.reset(token)

    def current(self) -> str:
        value = self._thread_id.get()
        if value is None:
            raise RuntimeError("active thread is unavailable")
        return value


class SkillApprovalAdapter:
    def __init__(self, approvals: ApprovalBroker) -> None:
        self._approvals = approvals

    async def approve(
        self, identifier: str, source: str, digest: str
    ) -> bool:
        return await self._approvals.request(
            ApprovalRequest(
                f"skill:{identifier}:{digest}",
                "activate_skill",
                {"identifier": identifier, "source": source, "digest": digest},
                "write",
                source,
                "workspace Skill instructions require explicit activation",
            ),
            CancellationToken(),
        )


class BoundSkillContextBuilder:
    """Bind caller identity and inject only the active thread's Skills."""

    def __init__(
        self,
        inner: object,
        binding: ThreadRuntimeBinding,
        skills: SkillController,
    ) -> None:
        self._semantic = inner
        self._inner = getattr(inner, "_inner", inner)
        self._binding, self._skills = binding, skills
        self._restored: set[str] = set()

    async def build(
        self,
        thread_id: str | ContextRequest,
        messages: Sequence[Message] | None = None,
        user_input: str | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        task_state: TaskState | None = None,
        cancellation: CancellationToken | None = None,
    ) -> object:
        if isinstance(thread_id, ContextRequest):
            if any(
                value is not None
                for value in (messages, user_input, tools, task_state, cancellation)
            ):
                raise TypeError("ContextRequest cannot be combined with legacy arguments")
            request = thread_id
        else:
            if messages is None or user_input is None or tools is None:
                raise TypeError("legacy context arguments are incomplete")
            if task_state is None or cancellation is None:
                raise TypeError("task_state and cancellation are required")
            request = ContextRequest(
                thread_id=thread_id,
                revision=1,
                messages=tuple(messages),
                user_input=user_input,
                tools=tuple(tools),
                task_state=task_state,
                cancellation=cancellation,
            )
        thread_id = request.thread_id
        self._binding.bind(thread_id)
        if thread_id not in self._restored:
            await self._skills.restore(thread_id)
            self._restored.add(thread_id)
        activation = self._skills.activation(thread_id)
        bundle = await self._semantic.build(request)
        content = activation.render()
        interaction = request.mode_snapshot.get("interaction_mode")
        mode_prompt = _INTERACTION_PROMPTS.get(str(interaction), "")
        additions = "\n\n".join(item for item in (content, mode_prompt) if item)
        from code_agent.context.measurements import with_system_prompt
        return with_system_prompt(
            bundle, bundle.system_prompt + ("\n\n" + additions if additions else ""))

    async def compact_context(
        self,
        thread_id: str,
        cancellation: CancellationToken | None = None,
    ) -> object:
        compact = getattr(self._semantic, "compact_context", None)
        if not callable(compact):
            raise RuntimeError("semantic context compaction is unavailable")
        return await compact(thread_id, cancellation)

    def semantic_snapshot_for_root(self, root: Path) -> object:
        config = getattr(self._inner, "config", None)
        workspace_root = getattr(config, "workspace_root", None)
        if workspace_root is None or Path(root).resolve() != workspace_root:
            raise ValueError("semantic snapshot root does not match context root")
        provider = getattr(self._inner, "semantic_snapshot_for_turn", None)
        if not callable(provider):
            raise RuntimeError("semantic snapshot is unavailable")
        return provider()


class ModelSemanticSummarizer:
    """Use the frozen task model and charge semantic summary usage to its task."""

    def __init__(self, model: object, model_name: str, sessions: object) -> None:
        self._model, self._model_name, self._sessions = (
            model,
            model_name,
            sessions,
        )

    async def summarize(
        self, request: SummaryRequest, cancellation: CancellationToken
    ) -> SummaryResponse:
        if not isinstance(request, SummaryRequest):
            raise TypeError("request must be a SummaryRequest")
        if not isinstance(cancellation, CancellationToken):
            raise TypeError("cancellation must be a CancellationToken")
        cancellation.raise_if_cancelled()
        source = _bounded_summary_source(request)
        messages = (
            Message(
                role="user",
                content=_SUMMARY_INSTRUCTION + source,
            ),
        )
        text: list[str] = []
        usage = Usage()
        async for event in self._model.stream(
            _SUMMARY_SYSTEM,
            messages,
            (),
        ):
            cancellation.raise_if_cancelled()
            if event.kind is ModelEventKind.TEXT_DELTA and event.text:
                text.append(event.text)
            elif event.kind is ModelEventKind.USAGE and event.usage is not None:
                usage = event.usage
        summary = "".join(text).strip()
        if not summary:
            raise RuntimeError("semantic summarizer returned no text")
        thread_id = request.sources[0].anchor.thread_id
        task = await self._sessions.load_task_for_thread(thread_id)
        if task is None:
            relation = await self._sessions.load_thread_relation(thread_id)
            if relation.parent_thread_id is not None:
                task = await self._sessions.load_task_for_thread(
                    relation.parent_thread_id
                )
        if task is not None:
            await self._sessions.consume_task_usage(task.id, usage)
        return SummaryResponse(summary, self._model_name, usage)


def _bounded_summary_source(request: SummaryRequest) -> str:
    fixed = (
        estimate_tokens(_SUMMARY_SYSTEM)
        + estimate_tokens(_SUMMARY_INSTRUCTION)
        + _SUMMARY_PROTOCOL_RESERVE
    )
    source_tokens = (
        request.max_total_tokens - request.max_output_tokens - fixed
    )
    if source_tokens <= 0:
        raise RuntimeError("semantic summary input budget is unavailable")
    return render_bounded_source_summary(request.sources, source_tokens)
