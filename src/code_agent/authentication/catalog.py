"""Explicit public model discovery; downloaded data never controls credentials."""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from .registry import API_PROTOCOLS, PROTOCOL_PATHS

PROVIDERS_URL = "https://pi.dev/api/models/providers"


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    provider: str
    protocol: str
    base_url: str
    request_path: str
    context_window: int = 128000
    max_output_tokens: int = 8192
    input_modalities: tuple[str, ...] = ("text",)
    experimental: bool = False


@dataclass(frozen=True)
class CatalogRefreshReport:
    providers: int
    models: int
    failed_providers: tuple[str, ...]


def _positive(value: object, fallback: int) -> int:
    return value if type(value) is int and value > 0 else fallback


def _model(provider: str, value: object) -> CatalogModel | None:
    if not isinstance(value, dict) or not isinstance(value.get("api"), str):
        return None
    if value["api"] not in API_PROTOCOLS:
        return None
    model_id, endpoint = value.get("id"), value.get("baseUrl")
    if not isinstance(model_id, str) or not model_id or not isinstance(endpoint, str):
        return None
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    if parsed.query or parsed.fragment or value.get("hidden") is True:
        return None
    protocol = API_PROTOCOLS[value["api"]]
    path = PROTOCOL_PATHS[protocol]
    if value["api"] == "antigravity":
        path = "/v1internal:streamGenerateContent"
    modalities = value.get("input", ["text"])
    if not isinstance(modalities, list):
        modalities = ["text"]
    if "text" not in modalities:
        return None
    return CatalogModel(
        model_id, str(value.get("name") or model_id), provider, protocol,
        endpoint.rstrip("/"), path, _positive(value.get("contextWindow"), 128000),
        _positive(value.get("maxTokens"), 8192),
        tuple(item for item in modalities if isinstance(item, str) and item in {"text", "image"}),
        value["api"] == "antigravity",
    )


def parse_provider_payload(provider: str, payload: object) -> tuple[CatalogModel, ...]:
    """Accept Pi's array, object-map, and models-array envelopes; ignore unknown APIs."""
    if isinstance(payload, dict):
        payload = payload.get("models", list(payload.values()))
    if not isinstance(payload, list):
        raise ValueError("Provider catalog must contain a model array or object map")
    models = (_model(provider, value) for value in payload)
    return tuple(model for model in models if model is not None)


def _write_cache(path: Path, data: dict[str, list[dict]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ModelCatalog:
    """Offline seed plus optional cache. Only refresh() performs network requests."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        seed = Path(__file__).with_name("catalog_seed.json")
        self._models: dict[str, tuple[CatalogModel, ...]] = {}
        self._load(seed)
        if self.path is not None and self.path.exists():
            self._load(self.path)

    def _load(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Invalid catalog cache")
        for provider, values in payload.items():
            self._models[provider] = parse_provider_payload(provider, values)

    def models(self, provider: str | None = None) -> tuple[CatalogModel, ...]:
        """List known compatible models; this is not a subscription entitlement check."""
        values = self._models.get(provider, ()) if provider else (
            model for models in self._models.values() for model in models
        )
        return tuple(sorted(values, key=lambda model: (model.provider, model.id)))

    async def refresh(self, client: httpx.AsyncClient | None = None) -> CatalogRefreshReport:
        """Refresh public Pi metadata, preserving cached providers on individual failure."""
        if client is None:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as owned:
                return await self.refresh(owned)
        response = await client.get(PROVIDERS_URL)
        response.raise_for_status()
        providers = response.json()
        if not isinstance(providers, list) or not all(
            isinstance(p, str) and re.fullmatch(r"[a-z0-9-]+", p) for p in providers
        ):
            raise ValueError("Invalid provider directory")
        failures: list[str] = []
        semaphore = asyncio.Semaphore(8)

        async def fetch(provider: str) -> None:
            async with semaphore:
                try:
                    reply = await client.get(PROVIDERS_URL + "/" + quote(provider, safe=""))
                    reply.raise_for_status()
                    self._models[provider] = parse_provider_payload(provider, reply.json())
                except (httpx.HTTPError, ValueError):
                    failures.append(provider)

        await asyncio.gather(*(fetch(p) for p in dict.fromkeys(providers)))
        if self.path is not None:
            await asyncio.to_thread(_write_cache, self.path, self._cache_payload())
        return CatalogRefreshReport(len(providers), len(self.models()), tuple(sorted(failures)))

    def _cache_payload(self) -> dict[str, list[dict]]:
        apis = {v: k for k, v in API_PROTOCOLS.items() if k != "antigravity"}
        return {provider: [
            {"id": m.id, "name": m.name, "api": "antigravity" if m.experimental else apis[m.protocol],
             "baseUrl": m.base_url, "contextWindow": m.context_window,
             "maxTokens": m.max_output_tokens, "input": list(m.input_modalities)}
            for m in models
        ] for provider, models in self._models.items()}
