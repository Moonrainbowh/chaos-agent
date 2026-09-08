"""API capacity/counting probe, explicitly not a task-quality benchmark."""
import asyncio
import json
import time
from pathlib import Path

from code_agent.config.loader import load_runtime_config
from code_agent.core.models import Message
from code_agent_win.managed_context import configured_counter
from code_agent_win.runtime_support import model_client


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", default="1000,32000,224000")
    args = parser.parse_args()
    runtime = load_runtime_config(cli_profile="gpt56_sol")
    profile = next(p for p in runtime.profiles if p.name == "gpt56_sol")
    counter = configured_counter(profile.provider.model)
    client = model_client(profile.provider, reasoning_effort="low", max_output_tokens=profile.max_output_tokens)
    results = []
    try:
        for size in map(int, args.sizes.split(",")):
            # Size-controlled synthetic payload tests transport/counter only.
            unit = "alpha beta gamma delta epsilon.\n"
            content = unit * max(1, (size - 100) // counter.text(unit))
            messages = (Message("user", content + "\nReply only OK."),)
            system = "This is a context capacity probe. Follow the final user request."
            estimate = counter.request(system, messages, ())
            item = {"requested_size": size, "estimated_input": estimate, "counter": counter.label,
                    "profile_combined_cap": profile.context_window, "output_reserve": profile.max_output_tokens}
            started = time.monotonic()
            try:
                async with asyncio.timeout(240):
                    async for event in client.stream(system, messages, ()):
                        if event.usage is not None:
                            item["usage"] = event.usage.to_dict()
                item["status"] = "completed" if "usage" in item else "usage_missing"
            except Exception as error:
                item["status"] = "failed"
                item["error_type"] = type(error).__name__
                item["error"] = str(error)[:500]
            item["seconds"] = round(time.monotonic() - started, 3)
            results.append(item)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(json.dumps(item), flush=True)
            if item["status"] != "completed":
                break
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
