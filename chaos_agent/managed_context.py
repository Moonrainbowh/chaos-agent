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
from chaos_agent.runtime_extensions import BoundSkillContextBuilder
from chaos_agent.context_assembly import ContextAssembly, ContextScopedDispatcher


def build_managed_context(config, rules, repo_map, skills, sessions, binding, client, profile, *,
                          memory_project_id=None, memory_user_scope_id=None,
                          allow_user_memory=False):
    """Compose managed context; memory scope must be supplied by the host.

    Keeping the identity explicit prevents a context builder from guessing a
    project from a directory name or accidentally enabling cross-project user
    memory.  Existing callers remain unchanged and therefore receive no
    memory projection until the host opts in with a verified identity.
    """
    policy = profile.context_policy
    limits = ApiContextLimits(profile.context_window, profile.max_output_tokens, profile.api_input_tokens)
    counter = configured_counter(profile.provider.model)
    guarded = client if isinstance(client, BudgetedWindowClient) else BudgetedWindowClient(
        client, sessions, binding.current, policy, limits, counter)
    workspace = WorkspaceContextBuilder(config, rules, repo_map, DeterministicCompactor(config))
    snapshot = workspace_snapshot(workspace, config.workspace_root)
    scaffold = BoundSkillContextBuilder(workspace, binding, skills, semantic_snapshot=snapshot)
    if policy.strategy == "persistent":
        result = PersistentContextBuilder(
            scaffold, sessions, policy, limits, counter, None,
            memory_project_id=memory_project_id,
            memory_user_scope_id=memory_user_scope_id,
            allow_user_memory=allow_user_memory,
        )
        actions = PersistentToolService(sessions, binding.current, result)
    else:
        result = WindowContextBuilder(scaffold, sessions, policy, limits, counter,
                                     HandoffWriter(guarded, counter, policy, limits))
        actions = WindowToolService(sessions, binding.current)
    return ContextAssembly(result, guarded, actions, snapshot)


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


def workspace_snapshot(workspace, root):
    """Bind snapshot access to the workspace built by this composition."""
    from pathlib import Path
    frozen_root = Path(root).resolve()
    def snapshot(requested_root):
        if Path(requested_root).resolve() != frozen_root:
            raise ValueError("semantic snapshot root does not match context root")
        return workspace.semantic_snapshot_for_turn()
    return snapshot


def wire_managed_engine(model, context, dispatcher):
    """Legacy direct composition helper; production uses ContextAssembly explicitly."""
    if not isinstance(context, ContextAssembly):
        raise TypeError("managed context wiring requires ContextAssembly")
    dispatcher.context_actions = context.context_actions
    return context.model_client
