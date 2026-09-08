"""Composition of opt-in managed windows with existing workspace services."""
from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context_windows.builder import WindowContextBuilder
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.counting import PromptTokenCounter
from code_agent.context_windows.handoff import HandoffWriter
from code_agent.context_windows.policy import ApiContextLimits
from code_agent.context_windows.tools import WindowToolService
from code_agent.context_windows.persistent_builder import PersistentContextBuilder
from code_agent.context_windows.persistent_tools import PersistentToolService
from code_agent_win.runtime_extensions import BoundSkillContextBuilder


def build_managed_context(config, rules, repo_map, skills, sessions, binding, client, profile):
    policy = profile.context_policy
    limits = ApiContextLimits(profile.context_window, profile.max_output_tokens, profile.api_input_tokens)
    counter = configured_counter(profile.provider.model)
    guarded = BudgetedWindowClient(client, sessions, binding.current, policy, limits, counter)
    workspace = WorkspaceContextBuilder(config, rules, repo_map, DeterministicCompactor(config))
    scaffold = BoundSkillContextBuilder(workspace, binding, skills)
    if policy.strategy == "persistent":
        result = PersistentContextBuilder(scaffold, sessions, policy, limits, counter, None)
        result.context_actions = PersistentToolService(sessions, binding.current, result)
    else:
        result = WindowContextBuilder(scaffold, sessions, policy, limits, counter,
                                     HandoffWriter(guarded, counter, policy, limits))
        result.context_actions = WindowToolService(sessions, binding.current)
    result.managed_client = guarded
    return result


def configured_counter(model):
    # A tokenizer estimate is not an API capability claim. Safety remains mandatory.
    encoding = None
    if model.startswith(("gpt-", "o3", "o4")):
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("o200k_base")
        except (ImportError, OSError):
            pass
    return PromptTokenCounter(encoding)


def wire_managed_engine(model, context, dispatcher):
    managed = context
    seen = set()
    while managed is not None and id(managed) not in seen:
        seen.add(id(managed))
        if getattr(managed, "managed_client", None) is not None:
            break
        managed = getattr(managed, "_inner", None)
    if managed is None or getattr(managed, "managed_client", None) is None:
        return model
    root = dispatcher
    while getattr(root, "_inner", None) is not None:
        root = root._inner
    root.context_actions = managed.context_actions
    return managed.managed_client
