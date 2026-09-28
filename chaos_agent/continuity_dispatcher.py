"""Restricted production workspace tools and a host-owned public verifier."""
import asyncio
import json
import os
import sys
import time

from code_agent.core.models import ActionResult, ToolDefinition
from code_agent.evaluation.observation import manifest_digest, workspace_manifest
from code_agent.evaluation.verifier import process_group_options, terminate_process_tree
from code_agent_win.context_experiment_host import ExperimentDispatcher


def digest(root):
    return manifest_digest(workspace_manifest(root))


class ContinuityDispatcher(ExperimentDispatcher):
    def __init__(self, root, state):
        super().__init__(root, ())
        self.state = state
        self.context_actions = None
        self.verified_digest = None
        self.calls = 0
        self.stage = None

    def tools(self):
        names = {"read_file", "write_file", "replace_text", "list_files", "search_text"}
        ordinary = tuple(tool for tool in self.inner.tools() if tool.name in names)
        memory = (tuple(tool for tool in self.context_actions.definitions() if tool.name != "new_context")
                  if self.context_actions else ())
        verify = ToolDefinition("run_verification", "Run the public Python unittest suite and return diagnostics.",
                                {"type": "object", "properties": {}, "additionalProperties": False})
        return ordinary + tuple(memory) + (verify,)

    async def dispatch(self, request, cancellation, *args, **kwargs):
        self.calls += 1
        if self.calls > 100:
            raise RuntimeError("continuity tool budget exceeded")
        started = time.monotonic()
        rejection = None
        if request.name in ("write_file", "replace_text"):
            rejection = ("diagnosis-read-only" if self.stage in (1, 3, 5) else
                         "protected-path" if not self._writable(request) else None)
        if rejection:
            message = ("This stage is read-only; inspect, plan, or verify without editing the workspace."
                       if rejection == "diagnosis-read-only" else "Only batch.py and new tests may change.")
            result = ActionResult(request.id, request.name, message, True)
        elif request.name == "run_verification":
            result = await self._verify(request)
        elif self.context_actions and request.name in self.context_actions.names:
            result = await self.context_actions.dispatch(request, cancellation)
        else:
            result = await self.inner.dispatch(request, cancellation, *args, **kwargs)
        entry = {"request": request.to_dict(), "result": result.to_dict(),
                 "stage": self.stage, "policy_rejection": rejection,
                 "process_instance": self.process_instance,
                 "seconds": round(time.monotonic() - started, 4), "workspace_digest": digest(self.root)}
        with (self.state / "actions.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return result

    def _writable(self, request):
        path = str(request.arguments.get("path", "")).replace("\\", "/")
        path = path.casefold()
        return path == "batch.py" or (path.startswith("tests/") and path != "tests/test_resume.py"
                                      and ".." not in path.split("/"))

    async def _verify(self, request):
        before = digest(self.root)
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
            cwd=self.root, env=environment, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, limit=65536, **process_group_options())
        try:
            output = await asyncio.wait_for(_bounded_output(process), 30)
        except BaseException:
            await terminate_process_tree(process)
            raise
        after = digest(self.root)
        passed = process.returncode == 0 and before == after
        self.verified_digest = after if passed else None
        return ActionResult(request.id, request.name,
                            {"passed": passed, "returncode": process.returncode,
                             "output": output.decode("utf-8", errors="replace")[-12000:],
                             "workspace_digest": after}, not passed)


async def _bounded_output(process):
    chunks, size = [], 0
    while chunk := await process.stdout.read(8192):
        size += len(chunk)
        if size > 65536:
            raise RuntimeError("public verifier output exceeded limit")
        chunks.append(chunk)
    await process.wait()
    return b"".join(chunks)
