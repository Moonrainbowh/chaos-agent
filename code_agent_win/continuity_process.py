"""Persistent bounded worker RPC, including observed process exit and restart."""
import asyncio
import json
import hashlib
import os
from dataclasses import replace
from pathlib import Path
import sys

from code_agent.evaluation.continuity_driver import Receipt
from code_agent.evaluation.verifier import process_group_options, terminate_process_tree


class ProcessContinuityAdapter:
    def __init__(self, workspace, state, options):
        self.workspace, self.state, self.options = workspace, state, options
        self.process = None
        self.last = None
        self.stats_before_pause = None
        self.receipts = []

    async def start(self):
        root = Path(__file__).resolve().parents[1]
        verify_freeze(root, required=self.options.mode == "api")
        args = [sys.executable, "-X", "utf8", "-B", "-u", "-m", "code_agent_win.continuity_worker",
                "--workspace", str(self.workspace), "--state", str(self.state)]
        for flag in ("mode", "profile", "model", "effort", "task_tokens"):
            args.extend(("--" + flag.replace("_", "-"), str(getattr(self.options, flag))))
        environment = {**os.environ, "PYTHONPATH": str(root / "src") + os.pathsep + str(root),
                       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        self.process = await asyncio.create_subprocess_exec(
            *args, cwd=root, env=environment, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            limit=1048576, **process_group_options())
        return await self._receipt("hello")

    async def _rpc(self, action, **arguments):
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError("worker is not running")
        self.process.stdin.write((json.dumps({"action": action, **arguments}) + "\n").encode("utf-8"))
        await self.process.stdin.drain()
        line = await asyncio.wait_for(self.process.stdout.readline(), self.options.timeout)
        if not line or len(line) > 1048576:
            raise RuntimeError("worker closed or exceeded controller response limit")
        response = json.loads(line)
        if response.get("ok") is not True:
            raise RuntimeError("worker failed: " + response.get("error_type", "unknown"))
        return response["value"]

    async def _receipt(self, action, **arguments):
        self.last = Receipt(**await self._rpc(action, **arguments))
        self.receipts.append({"action": action, **self.last.__dict__})
        return self.last

    async def work(self, workspace, message, rounds):
        if workspace != self.workspace:
            raise ValueError("worker workspace changed")
        if self.process is None:
            await self.start()
        return await self._receipt("work", message=message, rounds=rounds)

    async def set_stage(self, stage):
        if self.process is None:
            await self.start()
        await self._rpc("stage", stage=stage)

    async def switch(self):
        return await self._receipt("switch")

    async def pause(self):
        self.stats_before_pause = await self._rpc("stats")
        receipt = await self._receipt("pause")
        self.process.stdin.close()
        code = await asyncio.wait_for(self.process.wait(), 15)
        if code != 0:
            raise RuntimeError("worker did not exit cleanly")
        self.last = replace(receipt, process_exited=True)
        self.receipts[-1].update(process_exited=True, observed_exit_code=code)
        return self.last

    async def resume(self):
        if self.last is None or not self.last.process_exited:
            raise ValueError("cannot resume before confirmed exit")
        previous = self.last
        current = await self.start()
        if (current.task_id, current.store_id) != (previous.task_id, previous.store_id):
            raise ValueError("restarted worker lost persistent identity")
        return current

    async def stats(self):
        return await self._rpc("stats")

    async def close(self):
        if self.process is not None and self.process.returncode is None:
            await terminate_process_tree(self.process)


def verify_freeze(root, *, required):
    manifest = root / "runtime-freeze.json"
    if not manifest.exists():
        if required:
            raise ValueError("API benchmark requires a frozen runtime source snapshot")
        return
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    for relative, expected in files.items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen runtime source changed: " + relative)
