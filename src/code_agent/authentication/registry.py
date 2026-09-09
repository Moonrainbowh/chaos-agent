"""Offline provider metadata aligned with URI Agent's supported catalog APIs."""
from __future__ import annotations

from dataclasses import dataclass

API_PROTOCOLS = {
    "openai-responses": "responses", "openai-completions": "chat_completions",
    "anthropic-messages": "anthropic_messages", "openai-codex-responses": "codex_responses",
    "google-generative-ai": "google_generative_ai", "pi-messages": "pi_messages",
    "antigravity": "google_generative_ai",
}
PROTOCOL_PATHS = {
    "responses": "/responses", "chat_completions": "/chat/completions",
    "anthropic_messages": "/v1/messages", "codex_responses": "/codex/responses",
    "google_generative_ai": "/models/{model}:streamGenerateContent",
    "pi_messages": "/messages",
}
OAUTH_METHODS = {
    "antigravity": ("oauth",), "anthropic": ("oauth",), "workbuddy": ("workbuddy",),
    "openrouter": ("oauth",), "openai-codex": ("browser", "device_code"),
    "github-copilot": ("oauth",), "kimi-coding": ("oauth",), "muse-code": ("oauth",),
    "xai": ("oauth",), "radius": ("browser", "device_code"),
}


@dataclass(frozen=True)
class ProviderSpec:
    """Defaults only; per-model catalog metadata takes precedence."""

    id: str
    name: str
    protocol: str
    base_url: str
    api_key_env: str
    experimental: bool = False

    @property
    def oauth_methods(self) -> tuple[str, ...]:
        return OAUTH_METHODS.get(self.id, ())

    @property
    def offers_api_key(self) -> bool:
        return self.id not in {"antigravity", "openai-codex"}

    @property
    def login_methods(self) -> tuple[str, ...]:
        return self.oauth_methods + (("api_key",) if self.offers_api_key else ())

    @property
    def request_path(self) -> str:
        if self.id == "antigravity":
            return "/v1internal:streamGenerateContent"
        return PROTOCOL_PATHS[self.protocol]

    @property
    def required_parameters(self) -> tuple[str, ...]:
        if self.id == "cloudflare-ai-gateway":
            return ("account_id", "gateway_id")
        if self.id == "cloudflare-workers-ai":
            return ("account_id",)
        return ()


# Snapshot: pi.dev public catalog, 2026-09-09. No network at import time.
_ROWS = (
    ("abliteration", "responses", "https://api.abliteration.ai/v1", "ABLITERATION_API_KEY"),
    ("ant-ling", "chat_completions", "https://api.ant-ling.com/v1", "ANT_LING_API_KEY"),
    ("anthropic", "anthropic_messages", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    ("antigravity", "google_generative_ai", "https://cloudcode-pa.googleapis.com", ""),
    ("baseten", "chat_completions", "https://inference.baseten.co/v1", "BASETEN_API_KEY"),
    ("cerebras", "chat_completions", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    ("cloudflare-ai-gateway", "chat_completions", "https://gateway.ai.cloudflare.com/v1/{CLOUDFLARE_ACCOUNT_ID}/{CLOUDFLARE_GATEWAY_ID}/compat", "CLOUDFLARE_API_TOKEN"),
    ("cloudflare-workers-ai", "chat_completions", "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1", "CLOUDFLARE_API_KEY"),
    ("deepseek", "chat_completions", "https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    ("fireworks", "chat_completions", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY"),
    ("github-copilot", "chat_completions", "https://api.individual.githubcopilot.com", "COPILOT_GITHUB_TOKEN"),
    ("google", "google_generative_ai", "https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY"),
    ("groq", "chat_completions", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    ("huggingface", "chat_completions", "https://router.huggingface.co/v1", "HF_TOKEN"),
    ("kimi-coding", "anthropic_messages", "https://api.kimi.com/coding", "KIMI_API_KEY"),
    ("minimax", "anthropic_messages", "https://api.minimax.io/anthropic", "MINIMAX_API_KEY"),
    ("minimax-cn", "anthropic_messages", "https://api.minimaxi.com/anthropic", "MINIMAX_CN_API_KEY"),
    ("moonshotai", "chat_completions", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),
    ("moonshotai-cn", "chat_completions", "https://api.moonshot.cn/v1", "MOONSHOT_API_KEY"),
    ("muse-code", "responses", "https://api.meta.ai/v1", "MUSE_CODE_API_KEY"),
    ("nvidia", "chat_completions", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    ("openai", "responses", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    ("openai-codex", "codex_responses", "https://chatgpt.com/backend-api", ""),
    ("opencode", "chat_completions", "https://opencode.ai/zen/v1", "OPENCODE_API_KEY"),
    ("opencode-go", "chat_completions", "https://opencode.ai/zen/go/v1", "OPENCODE_API_KEY"),
    ("openrouter", "chat_completions", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    ("qwen-token-plan", "chat_completions", "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1", "QWEN_TOKEN_PLAN_API_KEY"),
    ("qwen-token-plan-cn", "chat_completions", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "QWEN_TOKEN_PLAN_CN_API_KEY"),
    ("qwen-token-plan-individual", "chat_completions", "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1", "QWEN_TOKEN_PLAN_API_KEY"),
    ("radius", "pi_messages", "https://radius.pi.dev/v1", "RADIUS_API_KEY"),
    ("together", "chat_completions", "https://api.together.ai/v1", "TOGETHER_API_KEY"),
    ("vercel-ai-gateway", "anthropic_messages", "https://ai-gateway.vercel.sh", "AI_GATEWAY_API_KEY"),
    ("workbuddy", "anthropic_messages", "https://copilot.tencent.com/v2", "CODEBUDDY_API_KEY"),
    ("xai", "responses", "https://api.x.ai/v1", "XAI_API_KEY"),
    ("xiaomi", "chat_completions", "https://api.xiaomimimo.com/v1", "XIAOMI_API_KEY"),
    ("xiaomi-token-plan-ams", "chat_completions", "https://token-plan-ams.xiaomimimo.com/v1", "XIAOMI_TOKEN_PLAN_AMS_API_KEY"),
    ("xiaomi-token-plan-cn", "chat_completions", "https://token-plan-cn.xiaomimimo.com/v1", "XIAOMI_TOKEN_PLAN_CN_API_KEY"),
    ("xiaomi-token-plan-sgp", "chat_completions", "https://token-plan-sgp.xiaomimimo.com/v1", "XIAOMI_TOKEN_PLAN_SGP_API_KEY"),
    ("zai", "chat_completions", "https://api.z.ai/api/coding/paas/v4", "ZAI_API_KEY"),
    ("zai-coding-cn", "chat_completions", "https://open.bigmodel.cn/api/coding/paas/v4", "ZAI_CODING_CN_API_KEY"),
)
_PROVIDERS = {row[0]: ProviderSpec(row[0], row[0], *row[1:], row[0] == "antigravity") for row in _ROWS}


def list_providers() -> tuple[ProviderSpec, ...]:
    """Return the offline supported provider registry."""
    return tuple(_PROVIDERS.values())


def get_provider(provider: str) -> ProviderSpec:
    """Resolve a known provider without guessing unknown service endpoints."""
    try:
        return _PROVIDERS[provider]
    except KeyError:
        raise ValueError(f"Unknown provider: {provider}") from None
