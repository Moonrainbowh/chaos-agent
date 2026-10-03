"""In-memory model choices backed by configured custom API profiles."""
from __future__ import annotations

from dataclasses import replace
from collections.abc import Callable, Mapping
from urllib.parse import quote, unquote
import shlex

from code_agent.providers.config import ApiProtocol, ModelProfile
from code_agent.providers.model_discovery import discover_models


class ApiModelControl:
    def __init__(self, profiles: Mapping[str, ModelProfile], register: Callable[[ModelProfile], None]) -> None:
        self._profiles = profiles
        self._register = register
        self._models: dict[str, tuple[str, ...]] = {}

    def _bases(self) -> dict[str, ModelProfile]:
        return {name: p for name, p in self._profiles.items()
                if not name.startswith(("login/", "discovered/"))
                and p.provider.auth_source is None
                and p.provider.api in {ApiProtocol.CHAT_COMPLETIONS, ApiProtocol.RESPONSES}}

    def choices(self) -> tuple[tuple[str, str], ...]:
        return tuple(("api:" + quote(name, safe=""), f"加载/刷新 API 模型 · {name}")
                     for name in self._bases())

    def is_refresh(self, instruction: str) -> bool:
        return instruction.strip() in {value for value, _ in self.choices()}

    def nested(self, instruction: str) -> tuple[tuple[str, str], ...] | None:
        parts = instruction.split(maxsplit=1)
        if not parts or not self.is_refresh(parts[0]):
            return None
        name = unquote(parts[0][4:])
        profile = self._bases()[name]
        detail = f"继承配置上限 · 上下文 {profile.context_window} · 输出 {profile.max_output_tokens} · {profile.provider.api.value}"
        return ((parts[0], "刷新 API 模型 · Enter"),) + tuple(
            (parts[0] + " " + shlex.quote(model), detail) for model in self._models.get(name, ()))

    async def refresh(self, instruction: str) -> int:
        name = unquote(instruction.strip()[4:])
        profile = self._bases()[name]
        models = await discover_models(profile.provider)
        self._models[name] = models
        return len(models)

    def is_model(self, instruction: str) -> bool:
        parts = shlex.split(instruction)
        return len(parts) == 2 and self.is_refresh(parts[0])

    def select(self, instruction: str) -> str:
        source, model = shlex.split(instruction)
        base = unquote(source[4:])
        if model not in self._models.get(base, ()):
            raise ValueError("Load this API's model catalog before selecting a model")
        return self._register_model(base, model)

    def _register_model(self, base: str, model: str) -> str:
        profile = self._bases()[base]
        if model == profile.provider.model:
            return base
        name = "discovered/" + quote(base, safe="") + "/" + quote(model, safe="")
        self._register(replace(profile, name=name, provider=replace(profile.provider, model=model)))
        return name

    def restore(self, name: str) -> None:
        if not name.startswith("discovered/"):
            return
        _, base, model = name.split("/", 2)
        self._register_model(unquote(base), unquote(model))
