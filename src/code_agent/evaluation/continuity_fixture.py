"""Frozen CSV task sources. Reference repair stays outside executor workspaces."""
from textwrap import dedent

VERSION = "csv-continuity-v1"

CONTRACT = """# CSV resume repair
Fix duplicate output and incorrect statistics after resuming an interrupted batch.
Keep run(source, output, checkpoint, limit=None) and the existing CLI flags unchanged.
source/output/checkpoint are paths. Input is UTF-8 CSV with id,amount headers;
amount is an integer, IDs may repeat, and fields contain no embedded newlines.
Input is immutable throughout processing. Each source data record is emitted once.
Output columns are line,id,amount; line is the physical source line (header is 1).
Checkpoint v1 is {"version": 1, "last_line": N}; N is the last committed physical
source line, initially 1. Existing output is an ordered, valid committed prefix.
Checkpoint can lag output after interruption, but cannot be ahead of output.
Preserve committed output: do not delete, truncate, or rewrite the output file.
Append only missing records; on a no-op resume its bytes must remain unchanged.
limit is None or a positive integer and limits NEW records for this invocation.
Return {"processed": cumulative output record count, "total": cumulative amount sum}.
Reject malformed amounts with ValueError without committing the malformed record.
Keep checkpoint v1 readable and write it atomically. No power-loss durability claim.
The reader exposes physical line numbers alongside each record.
Modify batch.py and add tests; preserve reader.py, storage.py, cli.py, and this contract.
Run: python -B -m unittest discover -s tests
"""

READER_V1 = dedent('''\
    import csv

    def records(source):
        with open(source, encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream)
            if next(reader, None) != ["id", "amount"]:
                raise ValueError("invalid header")
            for row in reader:
                if len(row) != 2:
                    raise ValueError("invalid record")
                yield reader.line_num, row[0], row[1]
''')
READER_V2 = READER_V1.replace(
    '            if len(row) != 2:',
    '            if not row:\n                continue\n            if len(row) != 2:',
)

STORAGE = dedent('''\
    import csv
    import json
    from pathlib import Path

    def load_output(path):
        if not Path(path).exists():
            return []
        with open(path, encoding="utf-8", newline="") as stream:
            return [(int(r["line"]), r["id"], int(r["amount"]))
                    for r in csv.DictReader(stream)]

    def append_output(path, row):
        exists = Path(path).exists()
        with open(path, "a", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\\n")
            if not exists:
                writer.writerow(["line", "id", "amount"])
            writer.writerow(row)

    def load_checkpoint(path):
        if not Path(path).exists():
            return 1
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data["version"] != 1:
            raise ValueError("unsupported checkpoint")
        return data["last_line"]

    def save_checkpoint(path, last_line):
        path = Path(path)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps({"version": 1, "last_line": last_line}),
                             encoding="utf-8")
        temporary.replace(path)
''')

FAULTY = dedent('''\
    from reader import records
    from storage import load_output, append_output, load_checkpoint, save_checkpoint

    def run(source, output, checkpoint, limit=None):
        last = load_checkpoint(checkpoint)
        processed = total = 0
        for index, (_, identity, raw) in enumerate(records(source), start=2):
            if index < last:
                continue
            value = int(raw)
            append_output(output, (index, identity, value))
            save_checkpoint(checkpoint, index)
            processed += 1
            total += value
            if limit is not None and processed >= limit:
                break
        return {"processed": processed, "total": total}
''')

GOLDEN = dedent('''\
    from reader import records
    from storage import load_output, append_output, load_checkpoint, save_checkpoint

    def run(source, output, checkpoint, limit=None):
        last = load_checkpoint(checkpoint)
        existing = load_output(output)
        last = max([last] + [row[0] for row in existing])
        processed = len(existing)
        total = sum(row[2] for row in existing)
        added = 0
        for line, identity, raw in records(source):
            if line <= last:
                continue
            value = int(raw)
            append_output(output, (line, identity, value))
            save_checkpoint(checkpoint, line)
            last = line
            processed += 1
            total += value
            added += 1
            if limit is not None and added >= limit:
                break
        save_checkpoint(checkpoint, last)
        return {"processed": processed, "total": total}
''')

CLI = dedent('''\
    import argparse
    import json
    from batch import run

    def main():
        parser = argparse.ArgumentParser()
        for flag in ("source", "output", "checkpoint"):
            parser.add_argument("--" + flag, required=True)
        parser.add_argument("--limit", type=int)
        args = parser.parse_args()
        print(json.dumps(run(args.source, args.output, args.checkpoint, args.limit)))

    if __name__ == "__main__":
        main()
''')

PUBLIC_TEST = dedent('''\
    import tempfile
    import unittest
    from pathlib import Path
    from batch import run

    class ResumeTests(unittest.TestCase):
        def test_resume(self):
            with tempfile.TemporaryDirectory() as directory:
                source, output, checkpoint = [Path(directory) / x for x in ("in.csv", "out.csv", "cp.json")]
                source.write_text("id,amount\\na,3\\nb,-1\\nc,5\\n", encoding="utf-8")
                self.assertEqual(run(source, output, checkpoint, 1), {"processed": 1, "total": 3})
                self.assertEqual(run(source, output, checkpoint), {"processed": 3, "total": 7})
                before = output.read_bytes()
                self.assertEqual(run(source, output, checkpoint), {"processed": 3, "total": 7})
                self.assertEqual(output.read_bytes(), before)
''')


def fixture_files() -> dict[str, str]:
    """Return only public input; never include GOLDEN or hidden tests."""
    return {"TASK.md": CONTRACT, "reader.py": READER_V1, "storage.py": STORAGE,
            "batch.py": FAULTY, "cli.py": CLI, "tests/test_resume.py": PUBLIC_TEST}
