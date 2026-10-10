"""Development-only parent-review model variant; no production registration."""
from contextlib import aclosing
import hashlib
import json
from types import MethodType
from urllib.parse import urlsplit

from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.policy import ApiContextLimits
from chaos_agent.managed_context import configured_counter

PROFILE = 'global:gpt-6-astra'
MODEL = 'global:gpt-6-astra'
EFFORT = 'medium'
OUTPUT_CAP = 4096


class ReviewVariant:
    """Adapt one already-selected main engine, restoring it after every turn.

    Install after all runtime/mode selections. Explicit aclose restores methods
    and closes only the additional provider. Existing unbound snapshots retain
    GLM; newly prepared snapshots freeze this experiment's exact identity.
    """

    def __init__(self, engine, client, identity):
        self.engine, self.client, self.identity = engine, client, identity
        self.requests = []
        self._base_model, self._base_name = engine._model, engine._model_name
        self._originals = {name: getattr(engine, name) for name in (
            '_prepare_parent_review', '_start_turn', '_run_turn',
            '_stream_model_events', '_record_model_usage')}
        self._active = False
        self._closed = False
        self.active_phase = None
        self.active_thread_id = None
        adapter = self

        async def prepare(this, state):
            host = this._parent_review
            existing = []
            if host is not None and state.task is not None:
                existing = [r for r in await host.sessions.context_records(
                    state.thread_id, 'parent_review') if r['task_id'] == state.task.id]
            await adapter._originals['_prepare_parent_review'](state)
            snapshot = state.parent_review
            if snapshot is None or not snapshot.active:
                return
            saved = snapshot.data.get('review_model')
            if saved is not None and saved != adapter.identity:
                raise ValueError('frozen review model identity drift')
            if not existing and saved is None and not snapshot.source_errors:
                state.parent_review = await host.record(state.task, snapshot,
                    {'review_model': dict(adapter.identity)})
                if state.parent_review.data.get('review_model') != adapter.identity:
                    raise ValueError('review model identity was not persisted')

        async def start(this, state, turn, user_input, bundles):
            snapshot = state.parent_review
            review = snapshot is not None and snapshot.active
            saved = snapshot.data.get('review_model') if review else None
            if saved is not None and saved != adapter.identity:
                raise ValueError('frozen review model identity drift')
            selected = review and saved is not None
            if selected and (snapshot.phase not in ('independent', 'comparison')
                             or turn.tools or turn.tool_names):
                raise ValueError('review variant requires a tool-free review phase')
            this._model = adapter.client if selected else adapter._base_model
            this._model_name = MODEL if selected else adapter._base_name
            adapter.active_phase = snapshot.phase if selected else None
            adapter.active_thread_id = state.thread_id if selected else None
            adapter.requests.append({'turn': turn.number, 'thread_id': state.thread_id,
                'phase': snapshot.phase if review else 'ordinary',
                'model': this._model_name, 'usage': []})
            async for event in adapter._originals['_start_turn'](
                    state, turn, user_input, bundles):
                yield event

        async def stream(this, state, turn, bundle):
            if this._model is adapter.client:
                expected = state.parent_review.bundle()
                if turn.tools or turn.tool_names or bundle != expected:
                    raise ValueError('isolated review context changed')
            async for event in adapter._originals['_stream_model_events'](state, turn, bundle):
                yield event

        async def usage(this, state, value):
            adapter.requests[-1]['usage'].append(value.to_dict())
            async for event in adapter._originals['_record_model_usage'](state, value):
                yield event

        async def run(this, state, number, user_input):
            if adapter._active:
                raise RuntimeError('review variant does not permit concurrent turns')
            adapter._active = True
            try:
                async with aclosing(adapter._originals['_run_turn'](
                        state, number, user_input)) as events:
                    async for event in events:
                        yield event
            finally:
                this._model, this._model_name = adapter._base_model, adapter._base_name
                adapter._active = False
                adapter.active_phase = adapter.active_thread_id = None

        for name, method in (('_prepare_parent_review', prepare), ('_start_turn', start),
                             ('_stream_model_events', stream),
                             ('_record_model_usage', usage), ('_run_turn', run)):
            setattr(engine, name, MethodType(method, engine))

    async def aclose(self):
        if self._closed:
            return
        if self._active:
            raise RuntimeError('close review variant after the active turn ends')
        self.engine._model, self.engine._model_name = self._base_model, self._base_name
        for name, method in self._originals.items():
            setattr(self.engine, name, method)
        await self.client.aclose()
        self._closed = True
        del self.engine._review_variant


async def install(app, profile=None, *, client_factory=None):
    """Install after team/mode selection; construction makes no HTTP request.

    profile may be supplied by the runner. Otherwise resolve the explicit
    configured profile; never resolve or expose credentials here.
    """
    if profile is None:
        from code_agent.config.loader import load_runtime_config
        config = load_runtime_config(cli_profile=PROFILE)
        profile = next(p for p in config.profiles if p.name == PROFILE)
    engine = app.controller._engine
    if hasattr(engine, '_review_variant'):
        raise ValueError('review variant is already installed')
    if engine._action_lineage is not None or engine._model_name != 'glm-5.3-flash':
        raise ValueError('review variant requires the original GLM main engine')
    base = engine._model
    if not isinstance(base, BudgetedWindowClient):
        raise TypeError('main engine has no shared prepared-request budget guard')
    limits = engine._limits
    if (limits.max_agent_rounds, limits.max_tool_calls, limits.max_total_tokens) != (12, 40, 1000000):
        raise ValueError('original task ceiling changed')
    if base.policy.task_tokens != 1000000 or base.limits.output_tokens != OUTPUT_CAP:
        raise ValueError('original request ceiling changed')
    if profile.provider.model != MODEL or profile.max_output_tokens < OUTPUT_CAP:
        raise ValueError('wrong review profile or output capacity')
    caps = ApiContextLimits(min(base.limits.combined_tokens, profile.context_window),
        OUTPUT_CAP, min(v for v in (base.limits.input_tokens, profile.api_input_tokens)
                        if v is not None) if any(v is not None for v in
                        (base.limits.input_tokens, profile.api_input_tokens)) else None)
    caps.input_cap(base.policy)
    identity = {'profile_id': profile.name, 'model': MODEL, 'protocol': profile.provider.api.value,
        'endpoint_host': urlsplit(profile.provider.base_url).hostname,
        'endpoint_digest': hashlib.sha256(profile.provider.base_url.encode()).hexdigest(),
        'timeout_s': profile.provider.timeout_s, 'max_retries': profile.provider.max_retries,
        'effort': EFFORT, 'output_cap': OUTPUT_CAP,
        'combined_tokens': caps.combined_tokens, 'input_tokens': caps.input_tokens,
        'task_tokens': base.policy.task_tokens,
        'host_prompt_tokens': base.constraints.host_prompt_tokens}
    identity['digest'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if client_factory is None:
        from chaos_agent.runtime_support import model_client
        client_factory = model_client
    raw = client_factory(profile.provider,
        reasoning_effort=EFFORT, max_output_tokens=OUTPUT_CAP,
        input_modalities=profile.input_modalities)
    guarded = BudgetedWindowClient(raw, base.sessions, base.current_thread, base.policy,
        caps, configured_counter(MODEL), constraints=base.constraints)
    adapter = ReviewVariant(engine, guarded, identity)
    engine._review_variant = adapter
    return adapter
