from __future__ import annotations

from collections.abc import Sequence

from code_agent.capabilities.catalog import CONTRACT_TOOL_NAME, contract_result
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition


class RestrictedDispatcher:
    """Expose only explicitly allowed root tools to one runner boundary."""

    def __init__(
        self,
        inner: object,
        allowed_tools: Sequence[str],
        *,
        allow_delegation: bool = False,
        allow_coordination: bool = False,
    ) -> None:
        if not isinstance(allow_delegation, bool):
            raise TypeError("allow_delegation must be a boolean")
        if not isinstance(allow_coordination, bool):
            raise TypeError("allow_coordination must be a boolean")
        self._inner = inner
        self._allow_delegation = allow_delegation
        self._allow_coordination = allow_coordination
        self._allowed = self._effective(allowed_tools)

    def replace_allowed(self, allowed_tools: Sequence[str]) -> None:
        self._allowed = self._effective(allowed_tools)

    def update_allowed(
        self, *, add: Sequence[str] = (), remove: Sequence[str] = ()
    ) -> None:
        updated = (self._allowed | self._validated(add)) - self._validated(remove)
        self._allowed = self._effective(tuple(updated))

    def _effective(self, values: Sequence[str]) -> frozenset[str]:
        checked = set(self._validated(values))
        if any(
            tool.name == CONTRACT_TOOL_NAME for tool in self._inner.tools()
        ):
            checked.add(CONTRACT_TOOL_NAME)
        if not self._allow_delegation:
            checked -= {"delegate_agent"}
        checked -= {"rename_agent"}
        if not self._allow_coordination:
            checked -= {"list_agents", "send_message"}
        return frozenset(checked)

    @staticmethod
    def _validated(values: Sequence[str]) -> frozenset[str]:
        if isinstance(values, (str, bytes)):
            raise TypeError("allowed tools must be a sequence of names")
        checked = tuple(values)
        for value in checked:
            if not isinstance(value, str):
                raise TypeError("allowed tool names must be text")
            if not value.strip():
                raise ValueError("allowed tool names must not be blank")
        return frozenset(checked)

    def tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            tool for tool in self._inner.tools() if tool.name in self._allowed
        )

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: object = None,
        *,
        execution_context: ActionExecutionContext | None = None,
    ) -> ActionResult:
        if request.name not in self._allowed:
            return ActionResult(
                request.id,
                request.name,
                {"error": "child tool is outside its mode and role"},
                is_error=True,
            )
        if request.name == CONTRACT_TOOL_NAME:
            return contract_result(request, self.tools())
        return await self._inner.dispatch(
            request,
            cancellation,
            task_authorization,
            execution_context=execution_context,
        )
