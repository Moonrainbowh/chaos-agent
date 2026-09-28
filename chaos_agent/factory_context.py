from __future__ import annotations

from collections.abc import Callable

from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.orchestration.models import ModeSnapshot

from code_agent_win.agent_modes import mode_prompt
from code_agent_win.app_models import FactoryHost
from code_agent_win.tool_support import windows_system_prompt


def build_factory_context(
    host: FactoryHost,
    mode: ModeSnapshot,
    context_factory: Callable[..., object],
    sessions: object | None = None,
) -> object:
    prompt = windows_system_prompt(
        host.git is not None, host.dispatcher.runtime.powershell_info()
    ) + "\n\n" + mode_prompt(mode)
    config = ContextConfig(
        host.root,
        host.root,
        prompt,
        repo_map_enabled=host.repo_map_enabled,
    )
    rules = RuleLoader(host.guard, host.files, config)
    repo_map = RepoMapBuilder(
        host.files,
        config,
        cache=host.cache,
        index=host.repo_index,
        view_cache=host.repo_view_cache,
    )
    return context_factory(
        config, rules, repo_map, host.skills, sessions or host.sessions
    )
