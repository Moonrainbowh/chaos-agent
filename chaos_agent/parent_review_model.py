"""Compose a separately configured review model under the parent request limits."""
import hashlib
import json
from dataclasses import asdict, replace
from urllib.parse import urlsplit

from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.policy import ApiContextLimits
from code_agent.core.parent_review_model import ParentReviewModel
from code_agent.providers.config import ApiProtocol
from .managed_context import configured_counter
from .runtime_support import model_client
from .runtime_selection_control import validate_profile_reasoning


class ReviewModelFactory:
    def __init__(self, profile, factory=model_client):
        self.profile, self.factory = profile, factory

    def __call__(self, base, parent_profile, mode):
        if not isinstance(base, BudgetedWindowClient):
            raise TypeError('parent review requires the shared request budget guard')
        profile = self.profile
        provider = replace(profile.provider,
            timeout_s=min(profile.provider.timeout_s, parent_profile.provider.timeout_s),
            max_retries=min(profile.provider.max_retries, parent_profile.provider.max_retries))
        policy = base.policy
        if profile.context_policy is not None:
            policy = replace(policy,
                work_tokens=min(policy.work_tokens, profile.context_policy.work_tokens),
                safety_tokens=max(policy.safety_tokens, profile.context_policy.safety_tokens),
                task_tokens=min(policy.task_tokens, profile.context_policy.task_tokens))
        inputs = [v for v in (base.limits.input_tokens, profile.api_input_tokens) if v is not None]
        caps = ApiContextLimits(min(base.limits.combined_tokens, profile.context_window),
            min(base.limits.output_tokens, profile.max_output_tokens), min(inputs) if inputs else None)
        base.constraints.input_cap(caps.input_cap(policy), policy.safety_tokens, caps.output_tokens)
        effort = mode.effective_reasoning_effort
        validate_profile_reasoning(profile, effort)
        identity = {
            'profile_id': profile.name, 'model': provider.model, 'protocol': provider.api.value,
            'endpoint_host': urlsplit(provider.base_url).hostname,
            'endpoint_digest': hashlib.sha256(provider.base_url.encode()).hexdigest(),
            'timeout_s': provider.timeout_s, 'max_retries': provider.max_retries,
            'effort': effort, 'output_cap': caps.output_tokens,
            'wire_effort': None if provider.api is ApiProtocol.ANTHROPIC_MESSAGES else effort,
            'combined_tokens': caps.combined_tokens, 'input_tokens': caps.input_tokens,
            'task_tokens': policy.task_tokens, 'policy': asdict(policy),
            'constraints': asdict(base.constraints),
            'host_prompt_tokens': base.constraints.host_prompt_tokens,
            'protocol_paths': {key: getattr(provider, key) for key in (
                'responses_path', 'chat_completions_path', 'anthropic_messages_path', 'pi_messages_path')},
        }
        identity['digest'] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        counter = configured_counter(provider.model)
        # Lazy construction means an unused review route opens no provider resources.
        raw = _ReviewClient(self.factory, provider, effort, caps.output_tokens, profile.input_modalities)
        guarded = BudgetedWindowClient(raw, base.sessions, base.current_thread,
            policy, caps, counter, constraints=base.constraints)
        return ParentReviewModel(guarded, provider.model, identity)


class _ReviewClient:
    """Prepared-request adapter whose provider belongs to this runtime only."""
    def __init__(self, factory, provider, effort, output, modalities):
        self.factory, self.provider = factory, provider
        self.effort, self.output, self.modalities = effort, output, modalities
        self.client = None
        self.closed = False

    def _client(self):
        if self.closed:
            raise RuntimeError('parent review provider is closed')
        if self.client is None:
            if self.factory is model_client:
                self.client = self.factory(self.provider, reasoning_effort=self.effort,
                    max_output_tokens=self.output, input_modalities=self.modalities)
            else:
                self.client = self.factory(self.provider)
        return self.client

    async def prepare_request(self, system_prompt, messages, tools):
        if tools:
            raise ValueError('parent review requests must be tool-free')
        return await self._client().prepare_request(system_prompt, messages, tools)

    def stream_prepared(self, request):
        return self._client().stream_prepared(request)

    async def aclose(self):
        if self.closed:
            return
        if self.client is not None:
            await self.client.aclose()
        self.closed = True


class RuntimeClients:
    """One runtime owner, with retryable close for every allocated provider."""
    def __init__(self, main, review):
        self.main, self.review = main, review
        self._closed = set()

    def __getattr__(self, name):
        return getattr(self.main, name)

    async def aclose(self):
        error = None
        for client in (self.review, self.main):
            if id(client) in self._closed:
                continue
            try:
                close = getattr(client, 'aclose', None)
                if callable(close):
                    await close()
                self._closed.add(id(client))
            except BaseException as failure:
                if error is None:
                    error = failure
        if error is not None:
            raise error


def own_context_clients(main, context):
    from .context_assembly import ContextAssembly
    review = context.parent_review_model if isinstance(context, ContextAssembly) else None
    return RuntimeClients(main, review.client) if review is not None else main
