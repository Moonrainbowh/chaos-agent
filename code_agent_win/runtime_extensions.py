from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar, Token
from dataclasses import replace

from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import Message, ModelEventKind, ToolDefinition, Usage
from code_agent.interfaces.approval import ApprovalBroker, ApprovalRequest
from code_agent.skills.controller import SkillController
from code_agent.thread_intelligence.models import (
    SummaryRequest,
    SummaryResponse,
)


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
        thread_id: str,
        messages: Sequence[Message],
        user_input: str,
        tools: Sequence[ToolDefinition],
        task_state: object,
        cancellation: CancellationToken,
    ) -> object:
        self._binding.bind(thread_id)
        if thread_id not in self._restored:
            await self._skills.restore(thread_id)
            self._restored.add(thread_id)
        activation = self._skills.activation(thread_id)
        bundle = await self._semantic.build(
            thread_id,
            messages,
            user_input,
            tools,
            task_state,
            cancellation,
        )
        content = activation.render()
        return replace(
            bundle,
            system_prompt=bundle.system_prompt
            + ("\n\n" + content if content else ""),
        )


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
        cancellation.raise_if_cancelled()
        source = "\n".join(
            f"[{item.anchor.stable_id}] {item.message.role}: {item.message.content}"
            for item in request.sources
        )
        messages = (
            Message(
                role="user",
                content=(
                    "Summarize the following task history faithfully. Preserve "
                    "decisions, constraints, unresolved work, evidence references, "
                    "and stable source IDs. Do not add facts.\n\n" + source
                ),
            ),
        )
        text: list[str] = []
        usage = Usage()
        async for event in self._model.stream(
            "You create compact semantic checkpoints for an ongoing coding task.",
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
