"""Post-run mechanical totals plus separately identified human review labels."""
import json
from pathlib import Path
import re

from semantic_harness import freeze_cases
from semantic_oracle import observe_case

HERE = Path(__file__).parent
RUN = HERE / "semantic-run-01"


def main():
    rows = []
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens_subset": 0,
              "total_tokens": 0, "usage_unknown_phases": 0,
              "compliance_correct": 0, "compliance_false_acceptance": 0,
              "compliance_false_rejection": 0, "compliance_unknown": 0,
              "copy_correct": 0, "copy_unknown": 0, "length_finish_phases": 0,
              "strict_json_unparseable_phases": 0}
    for case in freeze_cases():
        oracle = observe_case(case)
        for arm in ("A", "B"):
            directory = RUN / (case["id"] + "-" + arm)
            result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            for phase in result["phases"]:
                reply = phase["reply"]
                # Human-readable fenced JSON extraction is secondary analysis;
                # preserve original strict parser failures and original output.
                fenced = re.search(r"```json\s*(.*?)\s*```", reply["text"], re.S)
                parsed = json.loads(fenced.group(1) if fenced else reply["text"])
                decisions = parsed["compliance"]
                for key, expected in oracle["implementation_compliance"].items():
                    observed = decisions.get(key)
                    if type(observed) is not bool:
                        totals["compliance_unknown"] += 1
                    elif observed == expected:
                        totals["compliance_correct"] += 1
                    else:
                        totals["compliance_false_acceptance" if observed else "compliance_false_rejection"] += 1
                copy = parsed["copy_observation"]["distinct_object"]
                totals["copy_unknown" if copy is None else "copy_correct"] += 1
                if copy is not None and copy != oracle["copy_observation"]["distinct_object"]:
                    raise ValueError("Unexpected copy decision: manual review required")
                usage = reply.get("usage") or {}
                totals["usage_unknown_phases"] += int(not usage)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    totals[key] += usage.get(key, 0)
                totals["reasoning_tokens_subset"] += usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
                totals["strict_json_unparseable_phases"] += int(reply["score"]["status"] == "UNPARSEABLE_PRESERVED")
                finish = []
                for line in (directory / f"response-{phase['phase']}.sse").read_text(encoding="utf-8").splitlines():
                    if line.startswith("data:"):
                        try:
                            item = json.loads(line[5:])
                        except ValueError:
                            continue
                        for choice in item.get("choices", []):
                            if choice.get("finish_reason"):
                                finish.append(choice["finish_reason"])
                totals["length_finish_phases"] += int("length" in finish)
                rows.append({"case": case["id"], "arm": arm, "phase": phase["phase"],
                             "source": str((directory / "result.json").relative_to(HERE)),
                             "finish_reasons": finish, "copy_decision": copy,
                             "unknowns": parsed.get("unknowns", []),
                             "manual_semantic_review": "semantic-review.md"})
    value = {"scope": "LOCAL_SEMANTIC_EXPERIMENT_NOT_PRODUCTION_S16",
             "provider_requests": len(rows), "totals": totals, "phases": rows,
             "manual_labels": {
                 "copy_faulty_empty_universal_error_phases": 4,
                 "sorted_partial_A_dedup_cannot_detect_error_phases": 2,
                 "identity_B_unnecessary_copy_unknown_phases": 2,
                 "perfect_semantic_pair_passes": 0,
                 "note": "These are named claim-level human labels, not exhaustive prose error counts."},
             "limitations": ["one repetition per case/arm; no statistical inference",
                             "correct-advisory non-list wording is overbroad",
                             "strict JSON output contract violated in all phases",
                             "reasoning tokens included in completion; do not add twice"]}
    (HERE / "semantic-results.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(totals))


if __name__ == "__main__":
    main()
