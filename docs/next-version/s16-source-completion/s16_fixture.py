"""Reusable offline source fixture; preparing it never calls a Provider."""
import hashlib
import base64
import json
from pathlib import Path


ROOT = Path(__file__).parent / "source-fixture"


def load_fixture():
    """Load frozen inputs and verify every stored original source byte."""
    fixture = json.loads((ROOT / "fixture.json").read_text(encoding="utf-8"))
    for path, expected in fixture["source_sha256"].items():
        actual = hashlib.sha256(base64.b64decode(fixture["source_bytes_base64"][path], validate=True)).hexdigest()
        if actual != expected:
            raise ValueError("frozen source differs: " + path)
    return fixture


def prepare_workspace(destination):
    """Materialize a new/same fixture workspace; refuse conflicting files.

    Returns fixture inputs only. It does not start a Host/task, change budgets,
    install a plugin or invoke a Provider. required_source_paths is fixture
    metadata; callers explicitly pass it as the runtime required_sources.
    """
    fixture = load_fixture()
    destination = Path(destination).resolve()
    files = {path: base64.b64decode(fixture["source_bytes_base64"][path], validate=True)
             for path in fixture["source_sha256"]}
    files["AGENTS.md"] = (ROOT / "shared-rules.md").read_bytes()
    for path, content in files.items():
        target = destination / path
        if target.exists() and (not target.is_file() or target.read_bytes() != content):
            raise ValueError("fixture destination contains conflicting file: " + path)
    for path, content in files.items():
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
        if target.read_bytes() != content:
            raise ValueError("materialized fixture differs: " + path)
    return fixture


if __name__ == "__main__":
    fixture = load_fixture()
    print(json.dumps({"status": "PREPARED_FIXTURE_ONLY", "source_sha256": fixture["source_sha256"],
                      "runtime": fixture["runtime"]}, indent=2))
