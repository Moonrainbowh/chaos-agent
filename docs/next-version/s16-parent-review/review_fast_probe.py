"""Mechanical comparison of probe 1 and named manual observations of probe 2."""
import json
import re
from fast_semantic_probe import OUT


def main():
    replies = [json.loads((OUT / f"result-{n}.json").read_text(encoding="utf-8")) for n in (1, 2)]
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens_subset": 0,
              "total_tokens": 0, "usage_unknown": 0, "length_finish": 0}
    for number, reply in enumerate(replies, 1):
        usage = reply["usage"][-1] if reply["usage"] else {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            totals[field] += usage.get(field, 0)
        totals["reasoning_tokens_subset"] += usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
        totals["usage_unknown"] += int(not usage)
        finishes = []
        for line in (OUT / f"response-{number}.sse").read_text(encoding="utf-8").splitlines():
            if line.startswith("data:"):
                try:
                    item = json.loads(line[5:])
                except ValueError:
                    continue
                finishes.extend(c.get("finish_reason") for c in item.get("choices", []))
        totals["length_finish"] += int("length" in finishes)
    fence = re.search(r"```json\s*(.*?)\s*```", replies[0]["text"], re.S)
    parsed = json.loads(fence.group(1) if fence else replies[0]["text"])
    oracle = json.loads((OUT / "developer-oracle.json").read_text(encoding="utf-8"))
    comparisons = []
    for expected in oracle["concrete_outputs"]:
        row = next(r for r in parsed["results"] if r["input"] == expected["input"])
        actual = row[expected["function"]]
        comparisons.append({"function": expected["function"], "input": expected["input"],
                            "return_correct": actual["return"] == expected["output"],
                            "input_after_correct": actual["input_after"] == expected["input_after"],
                            "identity_correct": actual["same_object"] == expected["same_input_object"]})
    assert all(r[k] for r in comparisons for k in ("return_correct", "input_after_correct", "identity_correct"))
    value = {"scope": "TWO_LOCAL_STATIC_PROBES_NOT_PRODUCTION_S16", "provider_requests": 2,
             "usage": totals, "concrete_output_review": {"status": "PASS_12_OBSERVATIONS_36_FIELDS", "comparisons": comparisons},
             "assertion_scope_review": {"status": "PARTIAL_WITH_ERRORS",
                "correct": ["empty sentinel witness fails equality", "equal duplicates cannot reveal sorting",
                            "distinct C/A pair rejects sorted A/C output", "one sample does not prove universal order",
                            "three supplied assertions do not verify no input mutation"],
                "errors_or_missing": ["hardcoded K/K else [] incorrectly said to fail empty assertion",
                                      "always-empty claimed wrong on all other cases, ignoring all-blank nonempty input",
                                      "empty-input assertion confused with syntactically empty assertion",
                                      "third assertion lacks complete wrong-but-passing implementation and static trace"]},
             "strict_json_contract": "FAIL_TWO_FENCED_OUTPUTS_PRESERVED",
             "production_s16_acceptance": "NOT_ESTABLISHED_BY_THIS_LOCAL_PROBE"}
    (OUT / "review-results.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(totals))


if __name__ == "__main__":
    main()
