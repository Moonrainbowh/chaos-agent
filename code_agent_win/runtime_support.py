from __future__ import annotations

from code_agent.plugins.models import PluginRisk
from code_agent.providers.anthropic import AnthropicClient
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig
from code_agent.providers.openai_chat import OpenAIChatClient
from code_agent.providers.openai_responses import OpenAIResponsesClient


def model_client(config: ProviderConfig) -> object:
    if config.api is ApiProtocol.RESPONSES:
        return OpenAIResponsesClient(config)
    if config.api is ApiProtocol.CHAT_COMPLETIONS:
        return OpenAIChatClient(config)
    return AnthropicClient(config)


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
    )


def host_risks() -> dict[str, PluginRisk]:
    return {
        "read_file": PluginRisk.READ,
        "list_files": PluginRisk.READ,
        "search_text": PluginRisk.READ,
        "git_status": PluginRisk.READ,
        "git_diff": PluginRisk.READ,
        "write_file": PluginRisk.WRITE,
        "replace_text": PluginRisk.WRITE,
        "run_verification": PluginRisk.WRITE,
        "delegate_agent": PluginRisk.WRITE,
        "run_command": PluginRisk.CRITICAL,
    }
