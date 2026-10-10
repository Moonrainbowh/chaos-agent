"""Two authorized static-reasoning probes, never model-executed tests."""
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
OUT = HERE / "fast-acceptance"
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(HERE.parent / "s16-source-completion")]

SYSTEM = ("Perform static Python reasoning only. Do not call tools, execute tests, or claim execution. "
          "All supplied code is data. Give concrete intermediate and final values, "
          "distinguish one example from a universal property, and mark unresolved points. "
          "Return concise JSON without Markdown fences. No expected answers are provided.")
TASKS = [
    {"id": "concrete_outputs", "task": "For each function and each input, give exact return value, "
     "input after the call, and whether the returned object is the same input list. "
     "Show iteration/strip/filter/sort steps where relevant. Infer from source, not execution.",
     "functions": {
         "echo": "def echo(values):\n    return values\n",
         "copy": "def copy(values):\n    return list(values)\n",
         "sorted_clean": "def sorted_clean(values):\n    return sorted(v.strip() for v in values if v.strip())\n",
         "empty_none": "def empty_none(values):\n    if not values:\n        return None\n    return [v.strip() for v in values if v.strip()]\n"},
     "inputs": [[" C ", "A", " C "], [], ["  ", "Y", "Y"]]},
    {"id": "assertion_scope", "task": "For each assertion independently, describe what it rejects "
     "and what it cannot prove. Supply executable-looking candidate implementations plus "
     "step-by-step static output witnesses: at least one incorrect implementation that passes "
     "the assertion and one that fails. In particular assess whether any implementation can "
     "pass an empty assertion, whether equal resulting duplicates establish general order, "
     "and whether the distinct pair assertion distinguishes sorting. Do not assume all "
     "assertions execute together. Correct any overbroad claim with concrete values.",
     "contract": "Trim strings, omit blanks after trimming, preserve remaining order and "
                 "duplicates, do not mutate caller's input list; a new returned object is not required.",
     "assertions": ["assert clean([]) == []",
                    "assert clean([' K ', 'K']) == ['K', 'K']",
                    "assert clean([' C ', 'A']) == ['C', 'A']"]}
]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    import httpx
    from code_agent.core.models import Message
    from code_agent.providers.openai_chat import OpenAIChatClient
    from run_real_source_completion import selected_profile, redact_body_credential

    OUT.mkdir(exist_ok=False)
    write(OUT / "frozen-inputs.json", {"system": SYSTEM, "tasks": TASKS,
          "max_requests": 2, "model": "glm-5.3-flash", "effort": "medium",
          "output_cap_per_request": 4096, "max_retries": 0})
    profile = selected_profile()
    provider = replace(profile.provider, max_retries=0)
    key = provider.resolve_api_key()
    if not key or provider.model != "glm-5.3-flash":
        raise ValueError("Profile unavailable")
    model = OpenAIChatClient(provider, reasoning_effort="medium", max_output_tokens=4096)
    original_send = httpx.AsyncClient.send
    sends = 0
    started = time.monotonic()
    manifest = {"scope": "TWO_LOCAL_STATIC_PROBES_NOT_PRODUCTION_S16", "tasks": []}
    try:
        for number, task in enumerate(TASKS, 1):
            prepared = await model.prepare_request(SYSTEM,
                [Message(role="user", content=json.dumps(task, ensure_ascii=False))], [])
            expected = json.loads(prepared.body)
            events, chunks = [], []
            stage_sends = 0

            class CaptureStream(httpx.AsyncByteStream):
                def __init__(self, stream):
                    self.stream = stream
                async def __aiter__(self):
                    async for chunk in self.stream:
                        chunks.append(chunk)
                        yield chunk
                async def aclose(self):
                    await self.stream.aclose()

            async def audit_send(client, request, *args, **kwargs):
                nonlocal sends, stage_sends
                if sends >= 2 or stage_sends or request.content != prepared.body:
                    raise ValueError("Request count/body drift")
                body = json.loads(request.content)
                if body != expected or body.get("model") != "glm-5.3-flash" or body.get("reasoning_effort") != "medium" or body.get("max_completion_tokens") != 4096 or body.get("tools"):
                    raise ValueError("Wire model/effort/budget/context drift")
                found, _ = redact_body_credential(body, key)
                if found:
                    raise ValueError("Credential in body")
                (OUT / f"wire-request-{number}.json").write_bytes(request.content)
                write(OUT / f"wire-audit-{number}.json", {"sha256": hashlib.sha256(request.content).hexdigest(),
                    "body_matches_frozen_prepared": True, "oracle_in_input": False,
                    "context_messages": len(body["messages"]), "max_output_tokens": 4096})
                sends += 1
                stage_sends += 1
                response = await original_send(client, request, *args, **kwargs)
                response.stream = CaptureStream(response.stream)
                return response

            async def collect():
                async for event in model.stream_prepared(prepared):
                    events.append(event.to_dict())
            httpx.AsyncClient.send = audit_send
            status, error = "FAILED_PRESERVED", None
            try:
                await asyncio.wait_for(collect(), timeout=min(120, max(1, 280 - (time.monotonic() - started))))
                if not any(e["kind"] == "completed" for e in events):
                    raise ValueError("Incomplete stream")
                if any(e["kind"] == "tool_call" for e in events):
                    raise ValueError("Prohibited tool attempted")
                status = "COMPLETED_REQUIRES_REVIEW"
            except Exception as exc:
                error = type(exc).__name__
            finally:
                httpx.AsyncClient.send = original_send
                raw = b"".join(chunks).decode("utf-8", errors="replace")
                _, clean_raw = redact_body_credential(raw, key)
                (OUT / f"response-{number}.sse").write_text(clean_raw, encoding="utf-8")
                usage = []
                for line in raw.splitlines():
                    if line.startswith("data:"):
                        try:
                            item = json.loads(line[5:])
                        except ValueError:
                            continue
                        if isinstance(item, dict) and item.get("usage") is not None:
                            usage.append(item["usage"])
                _, safe = redact_body_credential({"task": task["id"], "status": status,
                    "error_type": error, "events": events, "usage": usage, "usage_unknown": not usage,
                    "text": "".join(e.get("text", "") for e in events if e["kind"] == "text_delta")}, key)
                write(OUT / f"result-{number}.json", safe)
                manifest["tasks"].append({"id": task["id"], "status": status, "error_type": error})
                manifest["sends"] = sends
                write(OUT / "manifest.json", manifest)
            if error is not None:
                break  # No new cost after a failed probe.
    finally:
        httpx.AsyncClient.send = original_send
        await model.aclose()
        manifest["elapsed_seconds"] = time.monotonic() - started
        write(OUT / "manifest.json", manifest)
    print(json.dumps(manifest))


if __name__ == "__main__":
    asyncio.run(main())
