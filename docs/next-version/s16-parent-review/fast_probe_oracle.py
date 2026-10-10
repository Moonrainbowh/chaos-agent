"""Developer execution only; excluded from model prompt and wire body."""
import json
from pathlib import Path

from fast_semantic_probe import TASKS, OUT


def main():
    rows = []
    for name, source in TASKS[0]["functions"].items():
        namespace = {}
        exec(source, namespace)
        for original in TASKS[0]["inputs"]:
            values = list(original)
            observed = namespace[name](values)
            rows.append({"function": name, "input": original, "output": observed,
                         "input_after": values, "same_input_object": observed is values})
    clean = lambda values: [v.strip() for v in values if v.strip()]
    candidates = {"always_empty": lambda values: [], "always_none": lambda values: None,
                  "sort_clean": lambda values: sorted(clean(values)),
                  "reverse_clean": lambda values: clean(values)[::-1],
                  "dedup_clean": lambda values: list(dict.fromkeys(clean(values))),
                  "identity": lambda values: values,
                  "hardcoded_pair": lambda values: ["C", "A"]}
    inputs = [([], []), ([" K ", "K"], ["K", "K"]), ([" C ", "A"], ["C", "A"])]
    matrix = {}
    for name, function in candidates.items():
        matrix[name] = [{"input": values, "output": function(list(values)),
                         "assertion_pass": function(list(values)) == expected}
                        for values, expected in inputs]
    evidence = {"scope": "DEVELOPER_EXECUTION_NOT_MODEL_EXECUTION", "provider_calls": 0,
                "concrete_outputs": rows, "assertion_witnesses": matrix}
    (OUT / "developer-oracle.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps({"provider_calls": 0, "observations": len(rows), "candidate_count": len(matrix)}))


if __name__ == "__main__":
    main()
