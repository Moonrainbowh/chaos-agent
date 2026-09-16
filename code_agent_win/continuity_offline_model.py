"""Scripted model ONLY for host wiring acceptance; never selected for API runs."""
import uuid

from code_agent.core.models import ModelEvent, ModelEventKind, ToolCall, Usage


class OfflineContinuityModel:
    def __init__(self):
        self.calls = 0

    async def stream(self, system_prompt, messages, tools):
        # Reference source is imported only in explicitly selected offline mode.
        from code_agent.evaluation.continuity_fixture import GOLDEN
        latest = next((str(m.content) for m in reversed(messages) if m.role == "user"), "")
        staged = "Diagnosis stage:" in latest or "Repair stage:" in latest
        evolved = "maintainer migrated" in latest or "migration repair" in latest
        if staged:
            from code_agent.evaluation.continuity_v2_fixture import GOLDEN_AFTER, GOLDEN_BEFORE
            GOLDEN = GOLDEN_AFTER if evolved else GOLDEN_BEFORE
        self.calls += 1
        if self.calls % 2 == 0:
            yield ModelEvent(ModelEventKind.TEXT_DELTA, text="Scripted wiring stage complete.")
            yield ModelEvent(ModelEventKind.USAGE, usage=Usage(input_tokens=500, output_tokens=20))
            yield ModelEvent(ModelEventKind.COMPLETED)
            return
        # Every slice repairs and exercises real tools. Durable notes are read after restart.
        available = {tool.name for tool in tools}
        requests = [("read_file", {"path": "TASK.md"}),
                    ("notes_read_file", {"path": "progress.md"}),
                    ("notes_write_file", {"path": "progress.md", "text": "Keep public interfaces. Verify current code."}),
                    ("history_search_contents", {"query": "TASK.md"}),
                    ("write_file", {"path": "batch.py", "content": GOLDEN}),
                    ("run_verification", {})]
        if staged:
            requests = [("read_file", {"path": "reader.py"}),
                        ("notes_read_file", {"path": "reader-contract.md"}),
                        ("history_search_contents", {"query": "reader"})]
            if "Repair stage:" in latest:
                requests += [("write_file", {"path": "batch.py", "content": GOLDEN}),
                             ("notes_write_file", {"path": "reader-contract.md", "text":
                              "Record(line, identity, amount); current reader.py inspected; verify migration."
                              if evolved else "tuple(line, identity, raw); reader.py inspected; migration remains pending."})]
            requests += [("run_verification", {})]
        for name, arguments in requests:
            if name in available:
                yield ModelEvent(ModelEventKind.TOOL_CALL,
                                 tool_call=ToolCall(uuid.uuid4().hex, name, arguments))
        yield ModelEvent(ModelEventKind.USAGE, usage=Usage(input_tokens=500, output_tokens=100))
        yield ModelEvent(ModelEventKind.COMPLETED)

    async def aclose(self):
        pass
