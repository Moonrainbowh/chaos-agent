"""TUI account login and explicit in-memory model registration."""
from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Callable, Mapping
from types import SimpleNamespace

from code_agent.authentication.catalog import ModelCatalog
from code_agent.authentication.models import AuthError, Credential
from code_agent.authentication.registry import get_provider, list_providers
from code_agent.authentication.store import CredentialStore
from code_agent.config.provider_settings import provider_config
from code_agent.providers.config import InputModality, ModelProfile

from .auth_cli import _key_metadata
from .auth_profile_setup import _endpoint


class AuthenticationRuntimeControl:
    """Login persists credentials; switching only registers a runtime profile."""

    def __init__(self, profiles: Mapping[str, ModelProfile],
                 register_profile: Callable[[ModelProfile], None],
                 store: CredentialStore | None = None) -> None:
        self._profiles = profiles
        self._register = register_profile
        self._store = store if store is not None else CredentialStore()
        self._saved_choices: tuple[tuple[str, str], ...] | None = None
        self._workbuddy_models = {}

    def login_choices(self) -> tuple[tuple[str, str], ...]:
        return tuple((f"{p.id} {method}",
                      ("Experimental · " if p.experimental else "") +
                      ("Hidden API Key input" if method == "api_key" else "Account authorization"))
                     for p in list_providers() for method in p.login_methods)

    def affects_profile(self, instruction: str, profile_name: str) -> bool:
        parts = _parts(instruction)
        if not 1 <= len(parts) <= 2 or profile_name not in self._profiles:
            return False
        platform = get_provider(parts[0])
        method = parts[1] if len(parts) == 2 else platform.login_methods[0]
        if method not in platform.login_methods:
            return False
        # OAuth-issued API keys still retain their OAuth credential slot.
        kind = "api_key" if method == "api_key" else "oauth"
        config = self._profiles[profile_name].provider
        return (config.provider_id == platform.id and config.auth_source is not None
                and config.auth_source.kind == kind)

    async def login(self, instruction: str, display, read_input) -> str:
        parts = _parts(instruction)
        if not 1 <= len(parts) <= 2:
            raise AuthError("Usage: /login provider [method|api_key]; enter secrets only in the hidden prompt")
        platform = get_provider(parts[0])
        method = parts[1] if len(parts) == 2 else platform.login_methods[0]
        if method not in platform.login_methods:
            raise AuthError("This login method is unavailable for the provider")
        if platform.experimental:
            display("This provider uses an experimental integration.")
        if method == "api_key":
            options = SimpleNamespace(provider=platform.id, account_id=None,
                                      gateway_id=None, gateway=None)
            if platform.id.startswith("cloudflare-"):
                options.account_id = await read_input("Cloudflare account ID: ")
                if platform.id == "cloudflare-ai-gateway":
                    options.gateway_id = await read_input("Cloudflare gateway ID: ")
            extra = _key_metadata(options)
            credential = Credential("api_key", await read_input("API Key (hidden): "), extra=extra)
        else:
            from code_agent.authentication.oauth import login
            credential = await login(platform.id, method=method, options={},
                                     display=display, read_input=read_input)
        # Once the atomic commit starts, report its outcome even if Esc arrives.
        # Cancelling to_thread alone would leave an unobserved credential write.
        commit = asyncio.create_task(asyncio.to_thread(self._commit, platform.id, credential))
        while not commit.done():
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                continue
        commit.result()
        result = f"Signed in to {platform.id} ({credential.kind}); use /logswitch to select a model."
        if platform.id == "workbuddy":
            result += " WorkBuddy models load when you select its entry in /logswitch."
        return result

    def _commit(self, provider: str, credential: Credential) -> None:
        self._store.set(provider, credential)
        if provider == "workbuddy":
            self._workbuddy_models.pop(credential.kind, None)
        try:
            self._reload_choices()
        except Exception:
            # Catalog corruption must not turn a committed login into a failure.
            self._saved_choices = None

    def switch_choices(self) -> tuple[tuple[str, str], ...]:
        choices = [(name, f"Configured · {profile.provider.model}") for name, profile in self._profiles.items()]
        if self._saved_choices is None:
            self._reload_choices()
        return tuple(choices) + (self._saved_choices or ())

    def _reload_choices(self) -> None:
        choices = []
        catalog = ModelCatalog(self._store.path.with_name("models-catalog.json"))
        for provider, kind, _ in self._store.status():
            if provider == "workbuddy":
                models = self._workbuddy_models.get(kind, ())
            else:
                models = catalog.models(provider)
            choices.extend((f"{provider}:{kind} {model.id}",
                            f"Signed in · {model.protocol} · {model.context_window} context")
                           for model in models)
            if provider == "workbuddy":
                choices.append((f"workbuddy:{kind}", "Load/refresh WorkBuddy account models · Enter"))
        self._saved_choices = tuple(choices)

    def is_refresh_choice(self, instruction: str) -> bool:
        return instruction.strip() in {"workbuddy:oauth", "workbuddy:api_key"}

    async def refresh_models(self, instruction: str) -> int:
        """Discover only the explicitly selected WorkBuddy credential slot."""
        if not self.is_refresh_choice(instruction):
            raise AuthError("Model discovery is unavailable for this selection")
        from code_agent.authentication.source import StoredCredentialSource
        from code_agent.authentication.workbuddy_catalog import discover
        kind = instruction.strip().split(":")[1]
        credential = await StoredCredentialSource("workbuddy", self._store.path, kind).resolve()
        models = await discover(credential)
        self._workbuddy_models[kind] = models
        await asyncio.to_thread(self._reload_choices)
        return len(models)

    async def restore_profile(self, name: str) -> None:
        """Recreate deterministic saved-login profiles for restored task contracts."""
        if not isinstance(name, str) or name in self._profiles or not name.startswith("login/"):
            return
        parts = name.split("/", 3)
        if len(parts) != 4:
            raise AuthError("Invalid saved login profile")
        await self.switch(f"{parts[1]}:{parts[2]} {shlex.quote(parts[3])}")

    async def switch(self, instruction: str) -> str:
        selected = instruction.strip()
        if selected in self._profiles:
            return selected
        parts = _parts(selected)
        if len(parts) != 2 or ":" not in parts[0]:
            raise AuthError("Usage: /logswitch profile or /logswitch provider:oauth|api_key model")
        provider, kind = parts[0].rsplit(":", 1)
        if kind not in {"oauth", "api_key"}:
            raise AuthError("Authentication must be oauth or api_key")
        platform = get_provider(provider)
        credential = await asyncio.to_thread(self._store.get, provider, kind)
        if credential is None:
            raise AuthError("No saved credentials for this method; use /login first")
        catalog = await asyncio.to_thread(ModelCatalog, self._store.path.with_name("models-catalog.json"))
        if provider == "workbuddy" and kind not in self._workbuddy_models:
            await self.refresh_models(f"workbuddy:{kind}")
        models = self._workbuddy_models.get(kind, ()) if provider == "workbuddy" else catalog.models(provider)
        model = next((m for m in models if m.id == parts[1]), None)
        if model is None:
            raise AuthError("Model is absent from the catalog; first use auth configure with explicit limits")
        name = f"login/{provider}/{kind}/{model.id}"
        fields = {"provider_id": platform.id, "auth": kind, "api": model.protocol,
                  "base_url": _endpoint(provider, model.base_url, credential), "model": model.id}
        path_field = {"responses": "responses_path", "codex_responses": "responses_path",
                      "chat_completions": "chat_completions_path", "anthropic_messages": "anthropic_messages_path",
                      "pi_messages": "pi_messages_path"}.get(model.protocol)
        if path_field:
            fields[path_field] = model.request_path
        config = provider_config(fields, {**os.environ, "CHAOS_AUTH_FILE": str(self._store.path)},
                                 allow_environment=False)
        profile = ModelProfile(name, config, model.context_window, model.max_output_tokens,
                               input_modalities=frozenset(InputModality(m) for m in model.input_modalities))
        self._register(profile)
        return name


def _parts(value: str) -> list[str]:
    try:
        return shlex.split(value)
    except ValueError:
        raise AuthError("Unclosed quote in login instruction") from None
