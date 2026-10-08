"""Pure observation mapping shared by source and task-bound dispatchers."""
from code_agent.core.models import ActionRequest


def resolve_plugin_action(request: ActionRequest, plugins: object) -> ActionRequest:
    """Keep opaque extension names unless the active Host maps a plugin target."""
    target = plugins.targets().get(request.name) if plugins else None
    return ActionRequest(request.id, target, request.arguments) if target else request
