"""Copy a stable runtime source snapshot without Git changes or private configuration."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def source_paths():
    paths = []
    for directory in ("src", "code_agent_win"):
        paths.extend(path for path in (ROOT / directory).rglob("*") if path.is_file()
                     and not {"__pycache__", "tests"}.intersection(path.parts)
                     and path.suffix in (".py", ".json", ".txt"))
    paths.extend(ROOT / "scripts" / name for name in
                 ("continuity_host_benchmark.py", "continuity_benchmark.py", "continuity_v2_benchmark.py",
                  "report_continuity_run.py", "freeze_continuity_runtime.py"))
    return sorted(paths)


def inventory():
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in source_paths()}


def freeze(destination):
    contents = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in source_paths()}
    before = {relative: hashlib.sha256(content).hexdigest() for relative, content in contents.items()}
    if before != inventory():
        raise RuntimeError("source changed while collecting snapshot; retry before API execution")
    destination.mkdir(parents=True, exist_ok=False)
    for relative in before:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents[relative])
    copied = {relative: hashlib.sha256((destination / relative).read_bytes()).hexdigest() for relative in before}
    if before != copied:
        raise RuntimeError("snapshot persistence mismatch; snapshot is not eligible for API runs")
    (destination / "runtime-freeze.json").write_text(json.dumps({"source": str(ROOT), "files": copied}, indent=2), encoding="utf-8")
    print(f"Frozen {len(copied)} runtime source files: {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    freeze(parser.parse_args().destination.resolve())
