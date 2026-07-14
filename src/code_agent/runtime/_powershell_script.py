from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


_EXIT_STATUS_EPILOGUE = """$__ChaosAgent_CommandSucceeded = $?
$__ChaosAgent_NativeExitCode = $LASTEXITCODE
if (-not $__ChaosAgent_CommandSucceeded) {
    if ($null -ne $__ChaosAgent_NativeExitCode -and $__ChaosAgent_NativeExitCode -ne 0) {
        exit $__ChaosAgent_NativeExitCode
    }
    exit 1
}
exit 0
"""


def render_powershell_script(script: str) -> str:
    """Append a fixed epilogue that preserves the final command status."""
    separator = "" if script.endswith(("\n", "\r")) else "\n"
    return script + separator + _EXIT_STATUS_EPILOGUE


@contextmanager
def temporary_powershell_script(script: str) -> Iterator[Path]:
    """Persist a PowerShell script for one execution and always remove it."""
    descriptor, raw_path = tempfile.mkstemp(suffix=".ps1")
    path = Path(raw_path)
    try:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

        stream = os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="")
        descriptor = -1
        with stream:
            stream.write(render_powershell_script(script))
            stream.flush()
            os.fsync(stream.fileno())
        yield path
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
