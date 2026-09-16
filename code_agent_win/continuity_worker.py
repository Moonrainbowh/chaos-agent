"""Line-delimited JSON controller channel; model text never controls the protocol."""
import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
import sys

from code_agent_win.continuity_runtime import ContinuityRuntime


async def serve(options):
    runtime = ContinuityRuntime()
    await runtime.initialize(options)
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            command = json.loads(line)
            action = command["action"]
            try:
                if action == "work":
                    value = asdict(await runtime.work(command["message"], command["rounds"]))
                elif action == "switch":
                    value = asdict(await runtime.switch())
                elif action == "stats":
                    value = await runtime.stats()
                elif action == "stage":
                    stage = command["stage"]
                    if stage not in (1, 2, 3, 4):
                        raise ValueError("invalid stage")
                    runtime.dispatcher.stage = stage
                    value = {"stage": stage}
                elif action in ("hello", "pause"):
                    value = asdict(await runtime.receipt())
                else:
                    raise ValueError("unsupported controller action")
                print(json.dumps({"ok": True, "value": value}), flush=True)
            except Exception as error:
                chain = []
                current = error
                while current is not None and len(chain) < 5:
                    chain.append(type(current).__name__)
                    current = current.__cause__
                (options.state / "worker-error.json").write_text(json.dumps({"error_types": chain}), encoding="utf-8")
                print(json.dumps({"ok": False, "error_type": type(error).__name__}), flush=True)
                return 1
            if action == "pause":
                break
    finally:
        await runtime.client.aclose()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--mode", choices=("offline", "api"), required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--task-tokens", type=int, required=True)
    options = parser.parse_args()
    options.workspace, options.state = options.workspace.resolve(), options.state.resolve()
    return asyncio.run(serve(options))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
