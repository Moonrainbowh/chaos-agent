from __future__ import annotations

from dataclasses import dataclass

from .models import CommandContribution
from .registry import PluginHost


@dataclass(frozen=True)
class PluginCommandInvocation:
    plugin_id: str
    digest: str
    generation: int
    qualified_id: str
    controller: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class PluginCommandDescriptor:
    plugin_id: str
    digest: str
    generation: int
    qualified_id: str
    description: str
    controller: str


class PluginCommandCatalog:
    def __init__(self, host: PluginHost) -> None:
        if not isinstance(host, PluginHost):
            raise TypeError("host must be PluginHost")
        self._host = host

    def list(self) -> tuple[PluginCommandDescriptor, ...]:
        result = []
        for registered in self._host.contributions("command"):
            command = registered.value
            if not isinstance(command, CommandContribution):
                continue
            result.append(
                PluginCommandDescriptor(
                    registered.plugin_id,
                    self._host.manifest_digest(registered.plugin_id),
                    self._host.generation,
                    registered.qualified_id,
                    command.description,
                    command.controller,
                )
            )
        return tuple(result)

    def resolve(
        self, qualified_id: str, arguments: tuple[str, ...] = ()
    ) -> PluginCommandInvocation:
        descriptor = next(
            (item for item in self.list() if item.qualified_id == qualified_id),
            None,
        )
        if descriptor is None:
            raise KeyError("plugin command is unavailable")
        checked = tuple(arguments)
        if any(not isinstance(value, str) for value in checked):
            raise TypeError("command arguments must be text")
        return PluginCommandInvocation(
            descriptor.plugin_id,
            descriptor.digest,
            descriptor.generation,
            descriptor.qualified_id,
            descriptor.controller,
            checked,
        )
