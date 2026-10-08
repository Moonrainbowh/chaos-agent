from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from code_agent.capabilities.catalog import CONTRACT_TOOL_NAME, contract_result
from code_agent.core.action_execution import ActionExecutionContext
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest, ActionResult, ToolDefinition
from code_agent.core.task import TaskAuthorization
from code_agent.policy.classifier import classify_action
from code_agent.policy.models import Capability
from code_agent.capabilities.compact_tools import OPERATIONS, compact_definitions, expand_request


class RestrictedDispatcher:
    """Expose only explicitly allowed root tools to one runner boundary."""

    def __init__(
        self,
        inner: object,
        allowed_tools: Sequence[str],
        *,
        allow_delegation: bool = False,
        allow_coordination: bool = False,
        compact_tools: bool = False,
        frozen_authorization: TaskAuthorization | None = None,
    ) -> None:
        if not isinstance(allow_delegation, bool):
            raise TypeError("allow_delegation must be a boolean")
        if not isinstance(allow_coordination, bool):
            raise TypeError("allow_coordination must be a boolean")
        self._inner = inner
        if frozen_authorization is not None and not isinstance(frozen_authorization, TaskAuthorization):
            raise TypeError("frozen_authorization must be TaskAuthorization")
        self._authorization = frozen_authorization
        self._compact = compact_tools
        self._allow_delegation = allow_delegation
        self._allow_coordination = allow_coordination
        self._allowed = self._effective(allowed_tools)

    def replace_allowed(self, allowed_tools: Sequence[str]) -> None:
        self._allowed = self._effective(allowed_tools)

    @property
    def verification_allow_sensitive_paths(self) -> bool:
        """Forward a read-only Host capability without exposing its editor."""
        return getattr(self._inner, "verification_allow_sensitive_paths", False)

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
        tools = self._legacy_tools()
        return compact_definitions(tools) if self._compact else tools

    def _legacy_tools(self):
        return tuple(
            tool for tool in self._inner.tools() if tool.name in self._allowed
        )

    def resolve_action(self, request):
        return expand_request(request, self._legacy_tools()) if self._compact else request

    def resolve_supervision_action(self, request):
        """Resolve observation identity without bypassing wrapper authorization."""
        resolved = self.resolve_action(request)
        resolver = getattr(self._inner, "resolve_supervision_action", None)
        return resolver(resolved) if callable(resolver) else resolved

    def compatible_action_names(self, definitions):
        """Accept old names only for operations in the currently disclosed schema."""
        allowed = set()
        if self._compact:
            for definition in definitions:
                operations = OPERATIONS.get(definition.name, {})
                exposed = definition.parameters.get("properties", {}).get("operation", {}).get("enum", ())
                allowed.update(operations[op] for op in exposed if op in operations)
        return allowed

    async def dispatch(
        self,
        request: ActionRequest,
        cancellation: CancellationToken,
        task_authorization: object = None,
        *,
        execution_context: ActionExecutionContext | None = None,
    ) -> ActionResult:
        original = request
        try:
            request = self.resolve_action(request)
        except ValueError as error:
            return ActionResult(original.id,original.name,{"error":str(error)},True)
        if request.name not in self._allowed:
            return ActionResult(
                request.id,
                original.name,
                {"error": "child tool is outside its mode and role"},
                is_error=True,
            )
        if self._authorization is not None:
            denied = self._authorization_denial(request, task_authorization)
            if denied is not None:
                return ActionResult(original.id, original.name, {"error": denied}, True)
            task_authorization = self._authorization
        if request.name == CONTRACT_TOOL_NAME:
            if self._compact:
                name = request.arguments.get("name")
                alias = next((group for group, ops in OPERATIONS.items() if name in ops.values()), name)
                if set(request.arguments) == {"name"} and alias != name:
                    request = ActionRequest(request.id,request.name,{"name":alias})
            return contract_result(request, self.tools())
        result = await self._inner.dispatch(
            request,
            cancellation,
            task_authorization,
            execution_context=execution_context,
        )
        if request is not original:
            return ActionResult(original.id,original.name,result.output,result.is_error,
                                {**result.metadata,"compact_target":request.name})
        return result

    def _authorization_denial(self, request, supplied):
        authorization = self._authorization
        if supplied is not None and supplied != authorization:
            return "child authorization differs from its frozen parent authority"
        resolver = getattr(self._inner, "resolve_supervision_action", None)
        translated = resolver(request) if callable(resolver) else request
        policy = getattr(self._inner, "policy", None)
        risks = getattr(getattr(policy, "config", None), "mcp_risks", {})
        limits = (
            (Capability.WRITE, authorization.allow_workspace_write),
            (Capability.EXECUTE, authorization.allow_local_execute),
            (Capability.NETWORK, authorization.allow_network),
            (Capability.OUTSIDE_WORKSPACE, authorization.allow_outside_workspace),
        )
        for item in (request, translated):
            capabilities = classify_action(item, Path(authorization.workspace_root), risks).capabilities
            if any(capability in capabilities and not allowed for capability, allowed in limits):
                return "child action exceeds frozen parent authorization"
        return None
