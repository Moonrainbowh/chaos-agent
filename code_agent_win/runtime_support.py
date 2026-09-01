from __future__ import annotations

from collections.abc import Callable, Mapping

from code_agent.providers.attachments import AttachmentResolver
from code_agent.plugins.models import PluginRisk
from code_agent.providers.anthropic import AnthropicClient
from code_agent.providers.config import (
    ApiProtocol,
    InputModality,
    ModelProfile,
    ProviderConfig,
)
from code_agent.providers.openai_chat import OpenAIChatClient
from code_agent.providers.openai_responses import OpenAIResponsesClient


def model_client(
    config: ProviderConfig,
    *,
    attachment_resolver: AttachmentResolver | None = None,
    input_modalities: frozenset[InputModality] = frozenset(
        {InputModality.TEXT}
    ),
    reasoning_effort: str | None = None,
    max_output_tokens: int = 4_096,
) -> object:
    options = {
        "attachment_resolver": attachment_resolver,
        "input_modalities": input_modalities,
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
    }
    if config.api is ApiProtocol.RESPONSES:
        return OpenAIResponsesClient(config, **options)
    if config.api is ApiProtocol.CHAT_COMPLETIONS:
        return OpenAIChatClient(config, **options)
    # No Anthropic reasoning-effort wire mapping is confirmed for this host.
    # Keep the frozen UI/audit choice, but do not invent a provider field.
    options["reasoning_effort"] = None
    return AnthropicClient(config, **options)


def profile_model_factory(
    factory: Callable[[object], object],
    profiles: Mapping[str, ModelProfile],
    attachment_resolver: AttachmentResolver | None,
) -> Callable[..., object]:
    """Bind profile capabilities while preserving one-argument test factories."""
    by_provider: dict[int, ModelProfile] = {}
    for profile in profiles.values():
        identity = id(profile.provider)
        previous = by_provider.setdefault(identity, profile)
        if previous.input_modalities != profile.input_modalities:
            raise ValueError(
                "shared provider configuration has conflicting input modalities"
            )

    def create(
        provider: object, *, reasoning_effort: str | None = None
    ) -> object:
        if factory is not model_client:
            return factory(provider)
        profile = by_provider.get(id(provider))
        if profile is None:
            raise ValueError("provider configuration is not bound to a profile")
        return model_client(
            profile.provider,
            attachment_resolver=attachment_resolver,
            input_modalities=profile.input_modalities,
            reasoning_effort=reasoning_effort,
            max_output_tokens=profile.max_output_tokens,
        )

    return create


def replace_model(profile: ModelProfile, model: str) -> ModelProfile:
    config = profile.provider
    provider = ProviderConfig(
        config.base_url,
        model,
        config.api,
        config.api_key_env,
        config.api_key_source,
        config.timeout_s,
        config.max_retries,
        config.max_event_bytes,
        config.max_response_bytes,
        config.max_tool_argument_bytes,
        config.max_tool_calls,
        config.responses_path,
        config.chat_completions_path,
        config.anthropic_messages_path,
    )
    return ModelProfile(
        profile.name,
        provider,
        profile.context_window,
        profile.max_output_tokens,
        profile.max_agent_rounds,
        profile.max_tool_calls,
        profile.max_tool_calls_per_round,
        profile.input_modalities,
    )


def host_risks() -> dict[str, PluginRisk]:
    return {
        "read_file": PluginRisk.READ,
        "read_code_slices": PluginRisk.READ,
        "list_files": PluginRisk.READ,
        "search_text": PluginRisk.READ,
        "git_status": PluginRisk.READ,
        "git_diff": PluginRisk.READ,
        "search_threads": PluginRisk.READ,
        "read_thread": PluginRisk.READ,
        "plan_workspace_edits_v1": PluginRisk.READ,
        "list_agents": PluginRisk.READ,
        "write_file": PluginRisk.WRITE,
        "replace_text": PluginRisk.WRITE,
        "apply_workspace_edit_plan_v1": PluginRisk.WRITE,
        "run_verification": PluginRisk.WRITE,
        "run_process_v1": PluginRisk.CRITICAL,
        "delegate_agent": PluginRisk.WRITE,
        "send_message": PluginRisk.WRITE,
        "run_command": PluginRisk.CRITICAL,
    }
