"""Archive-only provenance check; no Provider construction or imports."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
OUT = HERE / "fast-acceptance"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    original = HERE / "fast_semantic_probe.py"
    archived = OUT / "fast_semantic_probe.executed.py"
    if original.read_bytes() != archived.read_bytes():
        raise ValueError("Executed script snapshot differs")
    request_records = []
    for number in (1, 2):
        wire = OUT / f"wire-request-{number}.json"
        audit = json.loads((OUT / f"wire-audit-{number}.json").read_text(encoding="utf-8"))
        result = json.loads((OUT / f"result-{number}.json").read_text(encoding="utf-8"))
        if digest(wire) != audit["sha256"]:
            raise ValueError("Wire bytes changed")
        if not result["usage"] or result["usage_unknown"]:
            raise ValueError("Usage not preserved")
        stream = OUT / f"response-{number}.sse"
        raw_usages = []
        for line in stream.read_text(encoding="utf-8").splitlines():
            if line.startswith("data:"):
                try:
                    event = json.loads(line[5:])
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("usage") is not None:
                    raw_usages.append(event["usage"])
        if raw_usages != result["usage"]:
            raise ValueError("Saved usage differs from raw SSE")
        request_records.append({"request": number, "wire_sha256": digest(wire),
                                "sse_sha256": digest(stream),
                                "result_sha256": digest(OUT / f"result-{number}.json"),
                                "usage_records": len(raw_usages), "usage_matches_raw_sse": True})
    value = {"scope": "ARCHIVE_ONLY_NO_PROVIDER_CALL", "provider_calls": 0,
             "executed_script": str(original), "archive_snapshot": str(archived),
             "code_sha256": digest(original), "snapshot_exact_byte_match": True,
             "working_directory": str(ROOT),
             "exact_powershell_command": "& '.venv\\Scripts\\python.exe' docs\\next-version\\s16-parent-review\\fast_semantic_probe.py",
             "reproduction_note": "The unchanged script computes paths from its original location and refuses an existing fast-acceptance directory. Restore the exact snapshot at docs/next-version/s16-parent-review/fast_semantic_probe.py in an isolated equivalent checkout without that output directory. Do not invoke the nested archive snapshot directly or overwrite existing evidence.",
             "preserved_requests": request_records}
    (OUT / "script-provenance.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"provider_calls": 0, "code_sha256": value["code_sha256"],
                      "snapshot_exact_byte_match": True, "preserved_request_count": 2,
                      "usage_matches_raw_sse": True}))


if __name__ == "__main__":
    main()
