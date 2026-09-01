from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

from ._powershell_script import temporary_powershell_script
from .errors import RuntimeUnavailable
from .models import (
    CommandSpec,
    PowerShellRuntimeInfo,
    ShellDialect,
)


@contextmanager
def prepared_windows_command(
    spec: CommandSpec,
    powershell: PowerShellRuntimeInfo | None,
) -> Iterator[tuple[tuple[str, ...], str]]:
    if spec.argv is not None:
        yield spec.argv, subprocess.list2cmdline(spec.argv)
        return
    assert powershell is not None
    if spec.shell_script is not None:
        if spec.shell_script.dialect not in {
            ShellDialect.POWERSHELL_7,
            ShellDialect.WINDOWS_POWERSHELL_5_1,
        }:
            raise RuntimeUnavailable("local Windows runtime requires PowerShell")
        if spec.shell_script.dialect is not powershell.dialect:
            raise RuntimeUnavailable(
                "requested PowerShell dialect does not match the frozen runtime"
            )
        script = spec.shell_script.text
        display_command = f"<powershell-script:{powershell.dialect.value}>"
    else:
        assert spec.powershell_script is not None
        script = spec.powershell_script
        display_command = "<powershell-script>"
    with temporary_powershell_script(script) as script_path:
        argv = (
            powershell.executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script_path),
        )
        yield argv, display_command
