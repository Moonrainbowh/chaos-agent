from __future__ import annotations

from .command_registry import CommandSpec, REGISTRY


PaletteItem = CommandSpec


def filter_palette(input_text: str, *, limit: int = 6, services: set[str] | None = None) -> tuple[PaletteItem, ...]:
    return REGISTRY.filter(input_text, services, limit)
