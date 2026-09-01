from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CommandVisibility(str, Enum):
    PRIMARY = "primary"
    ADVANCED = "advanced"
    INTERNAL = "internal"


@dataclass(frozen=True)
class CommandAction:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str = ""
    source: str = "host"
    requires: tuple[str, ...] = ()
    visibility: CommandVisibility = CommandVisibility.PRIMARY


@dataclass(frozen=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...]
    group: str
    description: str
    usage: str = ""
    requires: tuple[str, ...] = ()
    accepts_active_task: bool = True
    actions: tuple[CommandAction, ...] = ()
    source: str = "host"
    controller: str | None = None
    plugin_id: str | None = None
    digest: str | None = None
    generation: int | None = None
    visibility: CommandVisibility = CommandVisibility.PRIMARY

    @property
    def display(self) -> str:
        return "/" + self.name + (" " + self.usage if self.usage else "")
