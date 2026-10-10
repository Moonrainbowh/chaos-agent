"""Explicit CLI execution of the frozen local experiment. Default is offline."""
import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

from semantic_harness import ROOT, S16, MODEL, EFFORT, OUTPUT_LIMIT
from semantic_harness import audit_request, freeze_cases, run_pair
from semantic_oracle import score_reply

PAIR_TOTAL_LIMIT = 60000
MAX_SENDS = 12


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


async def execute(output):
    # Credentials/config are read only after explicit --execute.
    sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(S16)]
    import httpx
    from code_agent.core.models import Message, ModelEventKind
    from code_agent.providers.openai_chat import OpenAIChatClient
    from run_real_source_completion import selected_profile, redact_body_credential

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    cases = freeze_cases()
    write(output / "frozen-cases.json", cases)
    manifest = {"scope": "LOCAL_SEMANTIC_EXPERIMENT_NOT_PRODUCTION_S16",
                "model": MODEL, "effort": EFFORT, "max_external_sends": MAX_SENDS,
                "pair_total_token_limit": PAIR_TOTAL_LIMIT, "request_timeout_seconds": 120,
                "failure_policy": "stop_failed_pair_continue_next_frozen_pair_no_retry",
                "pairs": [], "external_sends": 0}
    profile = selected_profile()
    provider = replace(profile.provider, max_retries=0)
    key = provider.resolve_api_key()
    if not key or provider.model != MODEL:
        raise ValueError("Required provider credentials/model unavailable")
    original_send = httpx.AsyncClient.send
    try:
        for case in cases:
            for arm in ("A", "B"):
                pair_output = output / (case["id"] + "-" + arm)
                spent = 0
                phase = 0
                model = OpenAIChatClient(provider, reasoning_effort=EFFORT, max_output_tokens=OUTPUT_LIMIT)

                async def send(planned):
                    nonlocal spent, phase
                    phase += 1
                    prepared = await model.prepare_request(planned["messages"][0]["content"],
                        [Message(role="user", content=planned["messages"][1]["content"])], [])
                    # Provider tokenizer unknown: reserve one token per UTF-8 byte
                    # of the complete prepared body, plus the full output cap.
                    # This conservative accounting never claims an exact token count.
                    reservation = len(prepared.body) + OUTPUT_LIMIT
                    if spent + reservation > PAIR_TOTAL_LIMIT:
                        raise ValueError("Common pair total allowance exhausted before send")
                    events, raw_chunks = [], []
                    phase_sends = 0

                    class CaptureStream(httpx.AsyncByteStream):
                        def __init__(self, stream):
                            self.stream = stream
                        async def __aiter__(self):
                            async for chunk in self.stream:
                                raw_chunks.append(chunk)
                                yield chunk
                        async def aclose(self):
                            await self.stream.aclose()

                    async def audited_send(client, request, *args, **kwargs):
                        nonlocal phase_sends, spent
                        if phase_sends or manifest["external_sends"] >= MAX_SENDS:
                            raise ValueError("External request count exceeded")
                        if request.content != prepared.body:
                            raise ValueError("Wire body differs from admitted prepared body")
                        body = json.loads(request.content)
                        found, _ = redact_body_credential(body, key)
                        if found:
                            raise ValueError("Credential in request body")
                        normalized = {"model": body.get("model"),
                                      "reasoning_effort": body.get("reasoning_effort"),
                                      "max_tokens": body.get("max_completion_tokens"),
                                      "messages": body.get("messages")}
                        if body.get("tools") or set(body) - {"model", "reasoning_effort", "max_completion_tokens", "messages", "stream", "stream_options"}:
                            raise ValueError("Unexpected wire context/tools")
                        audit = audit_request(case, arm, phase, normalized)
                        (pair_output / f"wire-request-{phase}.json").write_bytes(request.content)
                        write(pair_output / f"wire-audit-{phase}.json", {**audit,
                            "sha256": hashlib.sha256(request.content).hexdigest(),
                            "prepared_input_utf8_bytes": len(prepared.body),
                            "reservation": reservation, "total_allowance": PAIR_TOTAL_LIMIT,
                            "count_method": "conservative_utf8_bytes_plus_output_cap"})
                        spent += reservation  # Unknown/failed usage keeps reservation.
                        phase_sends += 1
                        manifest["external_sends"] += 1
                        response = await original_send(client, request, *args, **kwargs)
                        response.stream = CaptureStream(response.stream)
                        return response

                    httpx.AsyncClient.send = audited_send
                    async def collect():
                        async for event in model.stream_prepared(prepared):
                            events.append(event.to_dict())
                            if event.kind is ModelEventKind.TOOL_CALL:
                                raise ValueError("Model attempted prohibited tool call")
                    status = "FAILED_PRESERVED"
                    try:
                        await asyncio.wait_for(collect(), timeout=120)
                        if not any(e["kind"] == "completed" for e in events):
                            raise ValueError("Incomplete stream")
                        status = "COMPLETED"
                    finally:
                        httpx.AsyncClient.send = original_send
                        raw = b"".join(raw_chunks).decode("utf-8", errors="replace")
                        _, sanitized = redact_body_credential(raw, key)
                        (pair_output / f"response-{phase}.sse").write_text(sanitized, encoding="utf-8")
                        usages = []
                        for line in raw.splitlines():
                            if line.startswith("data:"):
                                try:
                                    item = json.loads(line[5:].strip())
                                except ValueError:
                                    continue
                                if isinstance(item, dict) and item.get("usage") is not None:
                                    usages.append(item["usage"])
                        _, clean_events = redact_body_credential(events, key)
                        _, usages = redact_body_credential(usages, key)
                        write(pair_output / f"events-{phase}.json", {"status": status,
                            "events": clean_events, "provider_usage": usages,
                            "usage_unknown": not usages, "reservation_retained": reservation})
                    text = "".join(e.get("text", "") for e in events if e["kind"] == "text_delta")
                    usage = usages[-1] if usages else {}
                    actual = usage.get("total_tokens")
                    if type(actual) is int and actual >= 0:
                        spent += max(0, actual - reservation)
                        if spent > PAIR_TOTAL_LIMIT:
                            raise ValueError("Reported actual total exceeds common allowance")
                    return {"text": text, "usage": usage or None, "usage_unknown": not usages,
                            "provider_usage_records": usages,
                            "score": score_reply(case, text), "budget_spent": spent}

                try:
                    result = await run_pair(case, arm, pair_output, send)
                    manifest["pairs"].append({"case": case["id"], "arm": arm, "status": result["status"]})
                except Exception as error:
                    # Avoid persisting provider exception strings which can contain URL/headers.
                    manifest["pairs"].append({"case": case["id"], "arm": arm,
                                              "status": "FAILED_PRESERVED", "error_type": type(error).__name__})
                finally:
                    await model.aclose()
                    write(output / "manifest.json", manifest)
    finally:
        httpx.AsyncClient.send = original_send
        write(output / "manifest.json", manifest)
    print(json.dumps({"external_sends": manifest["external_sends"], "pairs": manifest["pairs"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.execute:
        if args.output is None:
            parser.error("--execute requires a new --output directory")
        asyncio.run(execute(args.output))
    else:
        print(json.dumps({"status": "OFFLINE_ONLY", "provider_calls": 0,
                          "cases": [c["id"] for c in freeze_cases()], "max_requests": MAX_SENDS}))
