from __future__ import annotations

import unittest

from code_agent.runtime.models import (
    CommandSpec,
    PowerShellRuntimeInfo,
    PowerShellSelection,
    ShellDialect,
    ShellScript,
)


class ShellModelTests(unittest.TestCase):
    def test_typed_shell_script_is_the_third_exclusive_command_form(self) -> None:
        typed = ShellScript("Write-Output ready", ShellDialect.POWERSHELL_7)
        self.assertIs(CommandSpec(cwd=".", shell_script=typed).shell_script, typed)
        for values in (
            {"argv": ("python",), "shell_script": typed},
            {"powershell_script": "pwd", "shell_script": typed},
            {
                "argv": ("python",),
                "powershell_script": "pwd",
                "shell_script": typed,
            },
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    CommandSpec(cwd=".", **values)

    def test_shell_script_requires_an_explicit_dialect(self) -> None:
        with self.assertRaises(TypeError):
            ShellScript("pwd", "powershell_7")  # type: ignore[arg-type]

    def test_runtime_info_rejects_mismatched_identity(self) -> None:
        invalid = (
            (ShellDialect.POWERSHELL_7, "Desktop", "5.1.26100.1"),
            (ShellDialect.POWERSHELL_7, "Core", "6.2.0"),
            (ShellDialect.WINDOWS_POWERSHELL_5_1, "Core", "7.6.5"),
            (ShellDialect.WINDOWS_POWERSHELL_5_1, "Desktop", "5.2.0"),
        )
        for dialect, edition, version in invalid:
            with self.subTest(dialect=dialect, edition=edition, version=version):
                with self.assertRaises(ValueError):
                    PowerShellRuntimeInfo(
                        dialect, "C:\\shell.exe", PowerShellSelection.EXPLICIT,
                        edition, version,
                    )

    def test_public_info_hides_path_and_selection_source(self) -> None:
        info = PowerShellRuntimeInfo(
            ShellDialect.POWERSHELL_7,
            "C:\\Program Files\\PowerShell\\7\\pwsh.exe",
            PowerShellSelection.AUTO_PRIMARY,
            "Core",
            "7.6.5",
        )
        self.assertIn("C:\\Program Files", info.summary)
        self.assertIn("auto_primary", info.summary)
        self.assertEqual(
            info.prompt_summary,
            "powershell_7 (PowerShell 7.6.5, Core) via pwsh.exe",
        )
        self.assertEqual(info.to_public_dict()["executable"], "pwsh.exe")
        self.assertNotIn("selection", info.to_public_dict())

    def test_runtime_info_requires_absolute_executable(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute"):
            PowerShellRuntimeInfo(
                ShellDialect.POWERSHELL_7,
                "pwsh.exe",
                PowerShellSelection.EXPLICIT,
                "Core",
                "7.6.5",
            )


if __name__ == "__main__":
    unittest.main()
