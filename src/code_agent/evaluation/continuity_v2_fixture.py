"""Versioned dependency migration; v1 fixtures and scores remain unchanged."""
from dataclasses import replace
from textwrap import dedent

from . import continuity_fixture as v1
from .continuity_oracle import scenario as v1_scenario

VERSION = "csv-continuity-v2"
READER_V1 = v1.READER_V1
READER_V2 = dedent('''\
    import csv
    from dataclasses import dataclass

    API_VERSION = 2

    @dataclass(frozen=True)
    class Record:
        line: int
        identity: str
        amount: str

    def records(source):
        """Yield Record objects with physical source lines; skip blank rows."""
        with open(source, encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream)
            if next(reader, None) != ["id", "amount"]:
                raise ValueError("invalid header")
            for row in reader:
                if not row:
                    continue
                if len(row) != 2:
                    raise ValueError("invalid record")
                yield Record(reader.line_num, row[0], row[1])
''')
GOLDEN_BEFORE = v1.GOLDEN
GOLDEN_AFTER = v1.GOLDEN.replace(
    "    for line, identity, raw in records(source):",
    "    for record in records(source):\n"
    "        line, identity, raw = record.line, record.identity, record.amount",
)
CONTRACT = v1.CONTRACT.replace(
    "Modify batch.py and add tests; preserve reader.py, storage.py, cli.py, and this contract.",
    "Modify batch.py and add NEW test files only during repair stages. Existing tests,\n"
    "reader.py, storage.py, cli.py, and TASK.md are protected. Diagnosis stages permit\n"
    "inspection, verification, and History/Notes but no workspace edits.\n"
    "A separate maintainer may migrate the internal reader API between stages.\n"
    "Adapt batch.py to the installed reader; do not bypass or replace reader.records.\n"
    "The public batch/CLI/output/checkpoint contracts remain stable.",
)


def fixture_files():
    files = v1.fixture_files()
    files["TASK.md"] = CONTRACT
    return files


def behavior_oracle(root, evolved):
    oracle = v1_scenario(root, "C" if evolved else "A").expected.verifiers[1]
    source = next(iter(oracle.hidden_files.values()))
    tracing = ("import reader\n_reader_calls = []\n_original_records = reader.records\n"
               "def _observed_records(source):\n"
               "    _reader_calls.append(str(source))\n"
               "    return _original_records(source)\n"
               "reader.records = _observed_records\n")
    return replace(oracle, hidden_files={"_continuity_hidden.py": tracing + source +
                                         '\nassert _reader_calls, "reader.records bypassed"\n'})


def scenario(root, variant):
    """All four arms receive exactly the same migration and final behavior oracle."""
    if variant not in "ABCD" or len(variant) != 1:
        raise ValueError("unknown variant")
    case = v1_scenario(root, "C")
    oracle = replace(case.expected.workspace, exact_files={"reader.py": READER_V2})
    return replace(case, identifier=f"{VERSION}-{variant}", fixture_version=VERSION,
                   fixture_files=fixture_files(), golden_files={"batch.py": GOLDEN_AFTER},
                   expected=replace(case.expected, workspace=oracle,
                                    verifiers=(case.expected.verifiers[0], behavior_oracle(root, True))))
