"""Compose the existing engine and persistent context inside a worker process."""
import json
import os
import uuid
from dataclasses import asdict

from code_agent.capabilities import CapabilityStrategy
from code_agent.config.loader import load_runtime_config
from code_agent.context.builder import WorkspaceContextBuilder
from code_agent.context.compaction import DeterministicCompactor
from code_agent.context.models import ContextConfig
from code_agent.context.repo_map import RepoMapBuilder
from code_agent.context.rules import RuleLoader
from code_agent.context_windows.client import BudgetedWindowClient
from code_agent.context_windows.history import closed_group_ends
from code_agent.context_windows.persistent_builder import PersistentContextBuilder
from code_agent.context_windows.persistent_history import first_window_id
from code_agent.context_windows.persistent_tools import PersistentToolService
from code_agent.context_windows.policy import ApiContextLimits, WindowPolicy
from code_agent.core.cancellation import CancellationToken
from code_agent.core.context_request import ContextRequest
from code_agent.core.engine import AgentEngine
from code_agent.core.events import EventKind
from code_agent.core.limits import EngineLimits
from code_agent.evaluation.continuity_driver import Receipt
from code_agent.sessions.repository import SQLiteSessionRepository
from code_agent_win.continuity_dispatcher import ContinuityDispatcher
from code_agent_win.managed_context import configured_counter
from code_agent_win.runtime_support import model_client


def selected_profile(options):
    runtime = load_runtime_config(cli_profile=options.profile)
    profile = next(p for p in runtime.profiles if p.name == options.profile)
    if profile.provider.model != options.model:
        raise ValueError("configured model does not match frozen requested model")
    return profile


class ContinuityRuntime:
    async def initialize(self, options):
        self.options = options
        self.state, self.workspace = options.state, options.workspace
        self.state.mkdir(parents=True, exist_ok=True)
        self.sessions = SQLiteSessionRepository(self.state / "sessions.sqlite3")
        identity_path = self.state / "identity.json"
        if identity_path.exists():
            self.identity = json.loads(identity_path.read_text(encoding="utf-8"))
            if self.identity["workspace"] != str(self.workspace):
                raise ValueError("workspace identity changed on resume")
            await self.sessions.load_messages(self.identity["thread"])
        else:
            self.identity = {"thread": await self.sessions.create_thread(),
                             "store": uuid.uuid4().hex, "workspace": str(self.workspace)}
            identity_path.write_text(json.dumps(self.identity), encoding="utf-8")
        self.thread = self.identity["thread"]
        self.instance = f"{os.getpid()}:{uuid.uuid4().hex}"
        self.limits = EngineLimits(20, 100, 12, options.task_tokens)
        self.dispatcher = ContinuityDispatcher(self.workspace, self.state)
        self.dispatcher.process_instance = self.instance
        self._compose()

    def _compose(self):
        options = self.options
        if options.mode == "offline":
            from code_agent_win.continuity_offline_model import OfflineContinuityModel
            client, capacity = OfflineContinuityModel(), 400000
        else:
            profile = selected_profile(options)
            client = model_client(profile.provider, reasoning_effort=options.effort, max_output_tokens=8192)
            capacity = profile.context_window
        self.client = client
        policy = WindowPolicy(strategy="persistent", work_tokens=64000, safety_tokens=2000,
                              task_tokens=options.task_tokens)
        api_limits = ApiContextLimits(capacity, 8192)
        counter = configured_counter(options.model)
        config = ContextConfig(self.workspace, self.workspace,
                               "Complete the user's repository task using the provided tools. "
                               "Inspect current files and run_verification before reporting completion. "
                               "Controlled experiment: window boundaries are scheduled by the host; "
                               "do not request new_context.",
                               repo_map_enabled=False)
        dispatcher = self.dispatcher
        inner = WorkspaceContextBuilder(config, RuleLoader(dispatcher.guard, dispatcher.files, config),
                                        RepoMapBuilder(dispatcher.files, config), DeterministicCompactor(config))
        self.context = PersistentContextBuilder(inner, self.sessions, policy, api_limits, counter, None)
        dispatcher.context_actions = PersistentToolService(self.sessions, lambda: self.thread, self.context)
        guarded = BudgetedWindowClient(client, self.sessions, lambda: self.thread, policy, api_limits, counter)
        self.engine = AgentEngine(guarded, self.context, dispatcher, self.sessions, limits=self.limits,
                                  model_name=options.model, capability_strategy=CapabilityStrategy.LEGACY)

    async def receipt(self):
        records = await self.sessions.load_message_records(self.thread)
        windows = await self.sessions.context_records(self.thread, "window")
        return Receipt(self.thread, self.instance, windows[-1]["id"] if windows else first_window_id(self.thread),
                       self.identity["store"], tools_settled=closed_group_ends(records)[1],
                       verified_digest=self.dispatcher.verified_digest)

    async def work(self, message, rounds):
        budget = await self.sessions.get_or_create_task_budget(self.thread, self.options.model, self.limits)
        self.dispatcher.calls = budget.tool_calls
        stream = self.engine.run(message, thread_id=self.thread)
        turn = 0
        try:
            async for event in stream:
                with (self.state / "events.jsonl").open("a", encoding="utf-8") as target:
                    target.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                if event.kind == EventKind.TURN_STARTED:
                    turn += 1
                if turn >= rounds and event.kind == EventKind.MESSAGE_ADDED:
                    if (await self.receipt()).tools_settled:
                        break
        finally:
            await stream.aclose()
        return await self.receipt()

    async def switch(self):
        await self.context.compact_context(self.thread)
        budget = await self.sessions.get_or_create_task_budget(self.thread, self.options.model, self.limits)
        request = ContextRequest(self.thread, budget.model_turns + 1,
                                 tuple(await self.sessions.load_messages(self.thread)), "",
                                 self.dispatcher.tools(), await self.sessions.load_task_state(self.thread),
                                 CancellationToken())
        await self.context.build(request)
        return await self.receipt()

    async def stats(self):
        budget = await self.sessions.get_or_create_task_budget(self.thread, self.options.model, self.limits)
        return {"model_turns": budget.model_turns, "tool_calls": budget.tool_calls,
                "windows": list(await self.sessions.context_records(self.thread, "window")),
                "usage": list(await self.sessions.context_records(self.thread, "usage")),
                "receipt": asdict(await self.receipt())}
