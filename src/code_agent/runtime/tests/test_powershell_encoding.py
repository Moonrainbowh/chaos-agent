from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_agent.core.cancellation import CancellationToken
from code_agent.runtime._powershell_runtime import PowerShellRuntimeResolver
from code_agent.runtime._powershell_script import (
    render_powershell_wrapper,
    temporary_powershell_script,
)
from code_agent.runtime.errors import RuntimeUnavailable
from code_agent.runtime.local import WindowsLocalRuntime
from code_agent.runtime.models import (
    CommandSpec,
    ShellDialect,
    ShellScript,
    TerminationReason,
)

from _local_test_support import LocalRuntimeTestCase


class PowerShellEncodingWrapperTests(unittest.TestCase):
    def test_utf8_preamble_precedes_payload_without_file_defaults(self) -> None:
        payload_path = Path("user's payload.ps1")

        rendered = render_powershell_wrapper(payload_path)
        invocation = "& 'user''s payload.ps1'"

        self.assertLess(
            rendered.index("[Console]::InputEncoding"),
            rendered.index(invocation),
        )
        self.assertLess(
            rendered.index("[Console]::OutputEncoding"),
            rendered.index(invocation),
        )
        self.assertLess(rendered.index("$OutputEncoding ="), rendered.index(invocation))
        self.assertIn("[System.Text.UTF8Encoding]::new($false)", rendered)
        self.assertIn("$_.InvocationInfo.PositionMessage", rendered)
        self.assertIn("ActionPreference]::Stop", rendered)
        self.assertNotIn("ForEach-Object", rendered)
        self.assertNotIn("2>&1", rendered)
        self.assertNotIn("PSDefaultParameterValues", rendered)

    def test_payload_and_wrapper_retain_utf8_bom_and_are_cleaned(self) -> None:
        user_script = "Write-Output '\u4f60\u597d\U0001f642'"
        created: list[Path] = []
        original = tempfile.mkstemp

        def tracked(*args: object, **kwargs: object) -> tuple[int, str]:
            descriptor, raw_path = original(*args, **kwargs)
            created.append(Path(raw_path))
            return descriptor, raw_path

        with patch("tempfile.mkstemp", side_effect=tracked):
            with temporary_powershell_script(user_script) as wrapper_path:
                self.assertEqual(len(created), 2)
                payload_path, tracked_wrapper_path = created
                self.assertEqual(wrapper_path, tracked_wrapper_path)
                payload_raw = payload_path.read_bytes()
                wrapper_raw = wrapper_path.read_bytes()

        self.assertTrue(payload_raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(wrapper_raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(payload_raw.decode("utf-8-sig"), user_script)
        self.assertNotIn(user_script, wrapper_raw.decode("utf-8-sig"))
        self.assertTrue(all(not path.exists() for path in created))


@unittest.skipUnless(os.name == "nt", "PowerShell process encoding is Windows-only")
class RealPowerShellEncodingTests(LocalRuntimeTestCase):
    async def test_powershell_7_uses_utf8_without_user_setup(self) -> None:
        await self._assert_utf8_boundary(ShellDialect.POWERSHELL_7)

    async def test_windows_powershell_5_1_pipes_utf8_to_native(self) -> None:
        await self._assert_utf8_boundary(ShellDialect.WINDOWS_POWERSHELL_5_1)

    async def test_native_output_bytes_remain_exact_in_either_dialect(
        self,
    ) -> None:
        stdout = b"A\xffB\rC"
        stderr = b"E\xfeF\x00G"
        code = (
            "import sys;"
            "sys.stdout.buffer.write(bytes([65,255,66,13,67]));"
            "sys.stderr.buffer.write(bytes([69,254,70,0,71]))"
        )
        executable = str(Path(sys.executable)).replace("'", "''")
        script = f"& '{executable}' -c '{code}'"

        for dialect in self._dialects():
            with self.subTest(dialect=dialect.value):
                result = await self._run(dialect, script)
                self.assertEqual(result.reason, TerminationReason.EXITED)
                self.assertEqual(
                    result.returncode,
                    0,
                    (result.stdout, result.stderr),
                )
                self.assertEqual(result.stdout, stdout)
                self.assertEqual(result.stderr, stderr)

    async def _assert_utf8_boundary(self, dialect: ShellDialect) -> None:
        executable = str(Path(sys.executable)).replace("'", "''")
        native_reader = "import sys;print(sys.stdin.buffer.read().hex())"
        script = (
            "[Console]::Out.WriteLine('PSOUT:\u4f60\u597d\U0001f642')\n"
            "[Console]::Error.WriteLine('PSERR:\u9519\u8bef\U0001f642')\n"
            f"'PIPE:\u4f60\u597d\U0001f642' | & '{executable}' -c '{native_reader}'"
        )

        result = await self._run(dialect, script)

        expected_pipe = "PIPE:\u4f60\u597d\U0001f642\r\n".encode("utf-8").hex()
        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout.decode("utf-8", "strict").splitlines(),
            ["PSOUT:\u4f60\u597d\U0001f642", expected_pipe],
        )
        self.assertEqual(
            result.stderr.decode("utf-8", "strict").splitlines(),
            ["PSERR:\u9519\u8bef\U0001f642"],
        )

    @staticmethod
    def _dialects() -> tuple[ShellDialect, ...]:
        return (
            ShellDialect.POWERSHELL_7,
            ShellDialect.WINDOWS_POWERSHELL_5_1,
        )

    async def _run(self, dialect: ShellDialect, script: str):
        resolver = PowerShellRuntimeResolver(dialect)
        try:
            resolver.resolve()
        except RuntimeUnavailable:
            self.skipTest(f"{dialect.value} is unavailable")
        runtime = WindowsLocalRuntime(self.root, powershell=resolver)
        return await runtime.run(
            CommandSpec(
                cwd=".",
                shell_script=ShellScript(script, dialect),
            ),
            CancellationToken(),
            None,
        )


if __name__ == "__main__":
    unittest.main()
