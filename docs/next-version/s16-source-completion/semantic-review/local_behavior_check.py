"""Independent local oracle for the frozen example; never sent to the model."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / "source-fixture/fixture.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="s16-semantic-oracle-") as directory:
        temporary = Path(directory)
        for name in ("names.py", "test_names.py"):
            content = base64.b64decode(fixture["source_bytes_base64"][name], validate=True)
            assert hashlib.sha256(content).hexdigest() == fixture["source_sha256"][name]
            (temporary / name).write_bytes(content)
        names = load_module("names", temporary / "names.py")
        values = ["Z", "A", "Z"]
        original = list(values)
        observed = names.clean_names(values)
        behavior = {
            "trim": names.clean_names([" A "]) == ["A"],
            "omit_blank": names.clean_names([" ", "", "\t"]) == [],
            "preserve_order": observed == original,
            "preserve_duplicates": observed.count("Z") == 2,
            "no_input_mutation": values == original and observed is not values,
        }
        assert list(behavior.values()) == [False, False, True, True, True]
        sys.modules["names"] = names
        try:
            tests = load_module("frozen_test_names", temporary / "test_names.py")
            log = io.StringIO()
            result = unittest.TextTestRunner(stream=log).run(
                unittest.defaultTestLoader.loadTestsFromModule(tests)
            )
        finally:
            sys.modules.pop("names", None)
        assert result.testsRun == 5 and len(result.failures) == 3 and not result.errors
        # Wrong ordering implementations can satisfy existing example outputs.
        duplicate_expected = ["A", "A"]
        trim_expected = ["Alice", "Bob"]
        coverage = {
            "duplicate_example_rejects_reversal": duplicate_expected[::-1] != duplicate_expected,
            "trim_example_rejects_sorting": sorted(trim_expected) != trim_expected,
            "distinct_unsorted_example_rejects_sorting": sorted(original) != original,
        }
        assert list(coverage.values()) == [False, False, True]
        evidence = {
            "status": "PASS_LOCAL_ORACLE",
            "model_or_provider_calls": 0,
            "implementation_compliance": behavior,
            "frozen_fixture_tests": {"run": result.testsRun, "failures": len(result.failures),
                                     "errors": len(result.errors)},
            "coverage_discrimination": coverage,
            "scope": "Reviewer-only temporary copies; model remains read-only and tests-unexecuted.",
        }
        (Path(__file__).parent / "local-behavior-check.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(evidence))


if __name__ == "__main__":
    main()
