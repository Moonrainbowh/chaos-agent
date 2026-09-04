from __future__ import annotations

import shlex
from dataclasses import replace

from ._command_models import CommandAction, CommandSpec, CommandVisibility
from ._command_specs import built_in_command_specs


_SPECS = built_in_command_specs()


class CommandRegistry:
    def __init__(self, specs: tuple[CommandSpec, ...] = _SPECS) -> None:
        self._specs = specs
        self._lookup = {
            alias.casefold(): spec
            for spec in specs
            for alias in (spec.name, *spec.aliases)
        }
        if len(self._lookup) != sum(1 + len(item.aliases) for item in specs):
            raise ValueError("command aliases must be unique")

    def available(
        self, services: set[str] | None = None
    ) -> tuple[CommandSpec, ...]:
        services = services or set()
        return tuple(
            item
            for item in self._specs
            if set(item.requires).issubset(services)
        )

    def all(self) -> tuple[CommandSpec, ...]:
        return self._specs

    def primary(
        self, services: set[str] | None = None
    ) -> tuple[CommandSpec, ...]:
        values = self._specs if services is None else self.available(services)
        return tuple(
            item
            for item in values
            if item.visibility is CommandVisibility.PRIMARY
        )

    def with_plugin_commands(self, descriptors: object) -> "CommandRegistry":
        dynamic = []
        for descriptor in descriptors:
            qualified_id = descriptor.qualified_id
            if (
                not isinstance(qualified_id, str)
                or "." not in qualified_id
                or qualified_id.startswith(".")
                or qualified_id.endswith(".")
            ):
                raise ValueError("plugin commands must be namespaced")
            dynamic.append(
                CommandSpec(
                    qualified_id,
                    (),
                    "插件",
                    descriptor.description,
                    "[args...]",
                    requires=("plugins",),
                    source="plugin",
                    controller=descriptor.controller,
                    plugin_id=descriptor.plugin_id,
                    digest=descriptor.digest,
                    generation=descriptor.generation,
                    visibility=CommandVisibility.INTERNAL,
                )
            )
        return CommandRegistry(self._specs + tuple(dynamic))

    def with_plugin_modes(self, identifiers: object) -> "CommandRegistry":
        checked = tuple(identifiers)
        if any(
            not isinstance(identifier, str) or "." not in identifier
            for identifier in checked
        ):
            raise ValueError("plugin modes must be namespaced")
        specs = []
        for spec in self._specs:
            if spec.name not in {"mode", "模式"}:
                specs.append(spec)
                continue
            additions = tuple(
                CommandAction(
                    identifier,
                    (),
                    "Plugin restricted mode",
                    source="plugin",
                    requires=("modes",),
                )
                for identifier in checked
            )
            specs.append(replace(spec, actions=spec.actions + additions))
        return CommandRegistry(tuple(specs))

    def resolve(self, name: str) -> CommandSpec | None:
        return self._lookup.get(name.casefold())

    @staticmethod
    def resolve_action(spec: CommandSpec, name: str) -> CommandAction | None:
        query = name.casefold()
        return next(
            (
                action
                for action in spec.actions
                if query == action.name.casefold()
                or any(query == alias.casefold() for alias in action.aliases)
            ),
            None,
        )

    def parse(
        self, text: str, services: set[str] | None = None
    ) -> tuple[CommandSpec | None, tuple[str, ...], str | None]:
        if not isinstance(text, str):
            raise TypeError("command text must be a string")
        if not text.startswith(("/", ":")):
            return None, (), None
        try:
            parts = tuple(shlex.split(text[1:], posix=False))
        except ValueError:
            return None, (), "invalid quoted command"
        if not parts:
            return None, (), "command is required"
        spec = self._lookup.get(parts[0].casefold())
        if spec is None or spec not in self.available(services):
            return None, (), "unknown or unavailable slash command"
        return spec, parts[1:], None

    def filter(
        self,
        text: str,
        services: set[str] | None = None,
        limit: int = 6,
    ) -> tuple[CommandSpec, ...]:
        if not isinstance(text, str) or not text.startswith(("/", ":")):
            return ()
        query = text[1:].strip().casefold()
        return tuple(
            item
            for item in self.available(services)
            if item.visibility is CommandVisibility.PRIMARY
            and (
                query in item.name.casefold()
                or any(query in alias.casefold() for alias in item.aliases)
            )
        )[:limit]


REGISTRY = CommandRegistry()


__all__ = (
    "CommandAction",
    "CommandRegistry",
    "CommandSpec",
    "CommandVisibility",
    "REGISTRY",
)
