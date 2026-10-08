"""Explicit Host context composition and scoped central-dispatch services."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


_UNBOUND = object()
_actions = ContextVar("host_context_actions", default=_UNBOUND)


def current_context_actions(fallback=None):
    value = _actions.get()
    return fallback if value is _UNBOUND else value


@contextmanager
def context_actions_scope(service):
    token = _actions.set(service)
    try:
        yield
    finally:
        _actions.reset(token)


@dataclass(frozen=True)
class ContextAssembly:
    """One history builder, its guarded client and explicit snapshot capability."""

    builder: object
    model_client: object
    context_actions: object = None
    semantic_snapshot: object = None
    compact_binding: object = None

    def map_builder(self, wrapper):
        return replace(self, builder=wrapper(self.builder))

    async def build(self, *args, **kwargs):
        return await self.builder.build(*args, **kwargs)

    async def compact_context(self, *args, **kwargs):
        if self.compact_binding is None:
            return await self.builder.compact_context(*args, **kwargs)
        thread = args[0] if args else kwargs.get("thread_id")
        if not isinstance(thread, str) or not thread.strip():
            raise ValueError("compaction requires an explicit thread identity")
        token = self.compact_binding.bind(thread)
        try:
            return await self.builder.compact_context(*args, **kwargs)
        finally:
            self.compact_binding.reset(token)


class ContextScopedDispatcher:
    """Select context tools while preserving the original authorization router."""

    def __init__(self, dispatcher, actions):
        self.dispatcher, self.actions = dispatcher, actions

    def __getattr__(self, name):
        return getattr(self.dispatcher, name)

    def tools(self):
        with context_actions_scope(self.actions):
            return self.dispatcher.tools()

    def resolve_action(self, request):
        with context_actions_scope(self.actions):
            resolver = getattr(self.dispatcher, "resolve_action", None)
            return resolver(request) if callable(resolver) else request

    def resolve_supervision_action(self, request):
        with context_actions_scope(self.actions):
            resolver = getattr(self.dispatcher, "resolve_supervision_action", None)
            return resolver(request) if callable(resolver) else request

    async def dispatch(self, *args, **kwargs):
        with context_actions_scope(self.actions):
            return await self.dispatcher.dispatch(*args, **kwargs)


def wrap_context(context, wrapper):
    """Keep explicit capabilities when adding peer presentation context."""
    return context.map_builder(wrapper) if isinstance(context, ContextAssembly) else wrapper(context)
