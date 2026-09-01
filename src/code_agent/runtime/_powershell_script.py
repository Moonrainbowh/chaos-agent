from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


_UTF8_IO_PREAMBLE = """try {
    $__ChaosAgent_Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [Console]::InputEncoding = $__ChaosAgent_Utf8NoBom
    [Console]::OutputEncoding = $__ChaosAgent_Utf8NoBom
    $OutputEncoding = $__ChaosAgent_Utf8NoBom
} catch {
    [Console]::Error.WriteLine("chaos-agent failed to configure UTF-8 process I/O")
    exit 125
}
"""

_EXIT_STATUS_EPILOGUE = """$__ChaosAgent_NativeExitCode = $LASTEXITCODE
if ($null -ne $__ChaosAgent_NativeExitCode -and $__ChaosAgent_NativeExitCode -ne 0) {
    exit $__ChaosAgent_NativeExitCode
}
exit 0
"""


def render_powershell_wrapper(payload_path: Path) -> str:
    """Render the UTF-8 controller around a child-scope payload script."""
    quoted_payload = "'" + str(payload_path).replace("'", "''") + "'"
    invocation = (
        "$__ChaosAgent_HadPowerShellError = $false\n"
        "$__ChaosAgent_PreviousErrorActionPreference = "
        "$ErrorActionPreference\n"
        "$ErrorActionPreference = "
        "[System.Management.Automation.ActionPreference]::Stop\n"
        "try {\n"
        f"    & {quoted_payload}\n"
        "} catch {\n"
        "    $__ChaosAgent_HadPowerShellError = $true\n"
        "    [Console]::Error.WriteLine($_.ToString())\n"
        "    $__ChaosAgent_Position = $_.InvocationInfo.PositionMessage\n"
        "    if (-not [string]::IsNullOrWhiteSpace($__ChaosAgent_Position)) {\n"
        "        [Console]::Error.WriteLine($__ChaosAgent_Position)\n"
        "    }\n"
        "} finally {\n"
        "    $ErrorActionPreference = "
        "$__ChaosAgent_PreviousErrorActionPreference\n"
        "}\n"
        "if ($__ChaosAgent_HadPowerShellError -and "
        "($null -eq $LASTEXITCODE -or $LASTEXITCODE -eq 0)) {\n"
        "    exit 1\n"
        "}\n"
    )
    return _UTF8_IO_PREAMBLE + invocation + _EXIT_STATUS_EPILOGUE


@contextmanager
def _temporary_utf8_bom_script(contents: str) -> Iterator[Path]:
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
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        yield path
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)


@contextmanager
def temporary_powershell_script(script: str) -> Iterator[Path]:
    """Persist isolated payload/controller scripts and always remove both."""
    with _temporary_utf8_bom_script(script) as payload_path:
        wrapper = render_powershell_wrapper(payload_path)
        with _temporary_utf8_bom_script(wrapper) as wrapper_path:
            yield wrapper_path
