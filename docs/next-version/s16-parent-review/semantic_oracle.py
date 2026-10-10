"""Development-side executable observations; never attached to model inputs."""
import io
import json
import sys
import types
import unittest

from semantic_harness import freeze_cases


def compliance(function):
    values = ["Z", "A", "Z"]
    before = list(values)
    observed = function(values)
    return {"trim": function([" A "]) == ["A"],
            "omit_blank": function([" ", "", "\t"]) == [],
            "preserve_order": observed == before,
            "preserve_duplicates": observed.count("Z") == 2,
            "no_input_mutation": values == before}, {"distinct_object": observed is not values}


def run_source_tests(case, function):
    module = types.ModuleType("names")
    module.clean_names = function
    prior = sys.modules.get("names")
    sys.modules["names"] = module
    tests = types.ModuleType("semantic_source_tests")
    try:
        text = next(s["text"] for s in case["sources"] if s["path"] == "test_names.py")
        exec(compile(text, "test_names.py", "exec"), tests.__dict__)
        outcomes = {}
        for test_name in unittest.defaultTestLoader.getTestCaseNames(tests.NamesTests):
            result = unittest.TextTestRunner(stream=io.StringIO()).run(tests.NamesTests(test_name))
            outcomes[test_name] = {"pass": result.wasSuccessful(),
                                   "failures": len(result.failures), "errors": len(result.errors)}
        return outcomes
    finally:
        if prior is None:
            sys.modules.pop("names", None)
        else:
            sys.modules["names"] = prior


def observe_case(case):
    namespace = {}
    source = next(s["text"] for s in case["sources"] if s["path"] == "names.py")
    exec(compile(source, "names.py", "exec"), namespace)
    function = namespace["clean_names"]
    behavior, copy = compliance(function)
    clean = lambda values: [value.strip() for value in values if value.strip()]
    def mutate(values):
        values[:] = clean(values)
        return values
    probes = {"correct": clean, "returns_none": lambda values: None,
              "sorts_cleaned": lambda values: sorted(clean(values)),
              "reverses_cleaned": lambda values: clean(values)[::-1],
              "deduplicates": lambda values: list(dict.fromkeys(clean(values))),
              "mutates_input": mutate, "identity": lambda values: values}
    return {"case": case["id"], "implementation_compliance": behavior,
            "copy_observation": {**copy, "required_by_contract": False},
            "source_tests": run_source_tests(case, function),
            "counterexample_matrix": {key: run_source_tests(case, probe)
                                       for key, probe in probes.items()},
            "scope": "DEVELOPER_TEMPORARY_NAMESPACE_EXECUTION_NOT_MODEL_EXECUTION",
            "model_or_provider_calls": 0}


def score_reply(case, text):
    """Grade only explicit compliance decisions; prose evidence needs human review."""
    expected = observe_case(case)["implementation_compliance"]
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {"status": "UNPARSEABLE_PRESERVED", "raw": text}
    decisions = parsed.get("compliance", {})
    observations = {}
    for key, truth in expected.items():
        observed = decisions.get(key)
        if type(observed) is not bool:
            label = "unknown_or_missing"
        elif observed == truth:
            label = "correct"
        else:
            label = "false_acceptance" if observed else "false_rejection"
        observations[key] = {"expected": truth, "observed": observed, "label": label}
    return {"status": "DECISIONS_SCORED_PROSE_UNREVIEWED", "observations": observations,
            "tests_executed_claim": parsed.get("tests_executed"),
            "test_discrimination_requires_source_review": parsed.get("test_discrimination"),
            "comparison_requires_source_review": parsed.get("comparison")}


if __name__ == "__main__":
    print(json.dumps([observe_case(case) for case in freeze_cases()], indent=2))
