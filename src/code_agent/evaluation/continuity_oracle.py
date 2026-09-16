"""Hidden behavioral checks run only in an independent verifier clone."""
import sys
from pathlib import Path
from textwrap import dedent

from .continuity_fixture import GOLDEN, READER_V2, VERSION, fixture_files
from .models import Scenario, ScenarioExpectedOutcome, VerifierOracle, WorkspaceOracle

HIDDEN = dedent('''\
    import csv
    import inspect
    import json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    from batch import run

    def exercise(text, expected, lag=False):
        with tempfile.TemporaryDirectory() as directory:
            source, output, checkpoint = [Path(directory) / x for x in ("in.csv", "out.csv", "cp.json")]
            source.write_text(text, encoding="utf-8")
            first = run(source, output, checkpoint, 1)
            assert first == {"processed": 1, "total": int(expected[0][2])}
            prefix = output.read_bytes()
            if lag:
                checkpoint.write_text('{"version":1,"last_line":1}', encoding="utf-8")
            original_open = __import__("builtins").open
            original_unlink = Path.unlink
            def guarded_unlink(path, *args, **kwargs):
                assert path.resolve() != output.resolve(), "committed output removed"
                return original_unlink(path, *args, **kwargs)
            def guarded_open(file, mode="r", *args, **kwargs):
                if Path(file).resolve() == output.resolve():
                    assert not any(x in mode for x in ("w", "x", "+")), "output rewritten"
                return original_open(file, mode, *args, **kwargs)
            with patch("builtins.open", guarded_open), patch("io.open", guarded_open), patch.object(Path, "unlink", guarded_unlink):
                result = run(source, output, checkpoint)
            assert result == {"processed": len(expected), "total": sum(int(r[2]) for r in expected)}
            assert output.read_bytes().startswith(prefix)
            with output.open(newline="", encoding="utf-8") as stream:
                assert list(csv.reader(stream)) == [["line", "id", "amount"]] + expected
            assert json.loads(checkpoint.read_text()) == {"version": 1, "last_line": int(expected[-1][0])}
            before = output.read_bytes()
            assert run(source, output, checkpoint) == result
            assert output.read_bytes() == before
            command = [sys.executable, "-B", "cli.py", "--source", str(source),
                       "--output", str(output), "--checkpoint", str(checkpoint)]
            response = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
            assert json.loads(response.stdout) == result
            assert output.read_bytes() == before

    def invalid_amount():
        with tempfile.TemporaryDirectory() as directory:
            source, output, checkpoint = [Path(directory) / x for x in ("in.csv", "out.csv", "cp.json")]
            source.write_text("id,amount\\na,4\\nb,bad\\n", encoding="utf-8")
            run(source, output, checkpoint, 1)
            before = output.read_bytes()
            try:
                run(source, output, checkpoint)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid amount accepted")
            assert output.read_bytes() == before
            assert json.loads(checkpoint.read_text())["last_line"] == 2

    assert str(inspect.signature(run)) == "(source, output, checkpoint, limit=None)"
    for lag in (False, True):
        exercise('id,amount\\nx,4\\nx,-2\\n"a,b",9\\nz,0\\n',
                 [["2","x","4"],["3","x","-2"],["4","a,b","9"],["5","z","0"]], lag)
    invalid_amount()
    if EVOLVED:
        for lag in (False, True):
            exercise("id,amount\\n\\nx,4\\n\\nx,-2\\nz,9\\n\\n",
                     [["3","x","4"],["5","x","-2"],["6","z","9"]], lag)
''')


def scenario(root: Path, variant: str) -> Scenario:
    """Use the existing runner without altering the fixed forty-case corpus."""
    if variant not in ("A", "B", "C", "D"):
        raise ValueError("unknown variant")
    files = fixture_files()
    evolved = variant in ("C", "D")
    hidden = f"EVOLVED = {evolved!r}\n" + HIDDEN
    protected = tuple(path for path in files if path not in ("batch.py", "reader.py"))
    reader = READER_V2 if evolved else files["reader.py"]
    return Scenario(
        identifier=f"{VERSION}-{variant}", fixture_root=root, fixture_version=VERSION,
        user_request="Read TASK.md and repair CSV resume behavior.",
        category="continuity", fixture_files=files, golden_files={"batch.py": GOLDEN},
        max_active_seconds=900, max_model_turns=20, max_tool_calls=100,
        expected=ScenarioExpectedOutcome("completed", workspace=WorkspaceOracle(
            required_changes=("batch.py",), allowed_changes=("batch.py", "reader.py", "tests/*"),
            protected_files=protected, exact_files={"reader.py": reader}),
            verifiers=(VerifierOracle("public", (sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests")),
                       VerifierOracle("hidden", (sys.executable, "-B", "_continuity_hidden.py"),
                                      hidden_files={"_continuity_hidden.py": hidden}))),
    )
