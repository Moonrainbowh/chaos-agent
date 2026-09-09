from __future__ import annotations

from code_agent.authentication.catalog import ModelCatalog
from code_agent.authentication.models import AuthError
from code_agent.authentication.profile_setup import append_profile
from code_agent.authentication.registry import PROTOCOL_PATHS, get_provider
from code_agent.config.loader import resolve_config_path
from code_agent.providers.config import ApiProtocol


def configure(options, store) -> None:
    """Append a fully specified runnable profile without changing old defaults."""
    platform = get_provider(options.provider)
    credential = store.get(platform.id, options.auth)
    if credential is None:
        raise AuthError("先使用 auth login 登录该平台，再创建模型配置")
    matches = [item for item in ModelCatalog(store.path.with_name("models-catalog.json")).models(platform.id) if item.id == options.model]
    model = matches[0] if matches else None
    context = options.context_window if options.context_window is not None else (model.context_window if model else None)
    output = options.max_output_tokens if options.max_output_tokens is not None else (model.max_output_tokens if model else None)
    if context is None or output is None:
        raise AuthError("目录中没有此模型；请显式提供 --context-window 和 --max-output-tokens")
    protocol = options.api or (model.protocol if model else platform.protocol)
    try:
        ApiProtocol(protocol)
    except ValueError:
        raise AuthError("未知 API 协议") from None
    endpoint = options.base_url or (model.base_url if model else platform.base_url)
    endpoint = _endpoint(platform.id, endpoint, credential)
    fields = {
        "provider_id": platform.id, "auth": credential.kind,
        "api": protocol, "base_url": endpoint, "model": options.model,
        "context_window": context, "max_output_tokens": output,
        "input_modalities": list(model.input_modalities) if model else ["text"],
    }
    path_field = {"responses": "responses_path", "codex_responses": "responses_path",
                  "chat_completions": "chat_completions_path", "anthropic_messages": "anthropic_messages_path",
                  "pi_messages": "pi_messages_path"}.get(protocol)
    if path_field:
        fields[path_field] = model.request_path if model and protocol == model.protocol else PROTOCOL_PATHS[protocol]
    # Validate the same constructor used by the application before writing.
    from code_agent.config.loader import _provider_config
    import os
    _provider_config(fields, os.environ, allow_environment=False)
    path = resolve_config_path()
    name = options.profile or f"{platform.id}-{options.model}"
    append_profile(path, name, fields)
    print(f"已添加 profile {name}：{path}")
    print(f"使用 chaos-agent --profile \"{name}\" 启动。")


def _endpoint(provider: str, endpoint: str, credential) -> str:
    extra = credential.extra
    if provider == "radius" and extra.get("gateway"):
        from urllib.parse import urlsplit
        gateway = str(extra["gateway"]).rstrip("/")
        configured = urlsplit(endpoint)
        bound = urlsplit(gateway)
        if configured.netloc != bound.netloc:
            # Default Radius endpoint follows the explicitly chosen login gateway.
            if endpoint == "https://radius.pi.dev/v1":
                endpoint = gateway + "/v1"
            else:
                raise AuthError("Radius model endpoint must match the login gateway")
    if provider == "workbuddy" and extra.get("workbuddyEndpoint"):
        endpoint = str(extra["workbuddyEndpoint"]).rstrip("/")
    if provider.startswith("cloudflare-"):
        account, gateway = extra.get("accountId"), extra.get("gatewayId", "default")
        if not account:
            raise AuthError("Cloudflare 凭据缺少 account ID，请重新登录并提供 --account-id")
        endpoint = endpoint.replace("{CLOUDFLARE_ACCOUNT_ID}", str(account)).replace("{CLOUDFLARE_GATEWAY_ID}", str(gateway))
        if "{" in endpoint:
            raise AuthError("Cloudflare endpoint 尚有未填参数")
    return endpoint
