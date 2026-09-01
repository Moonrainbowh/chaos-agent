from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.runtime.errors import (  # noqa: E402
    RuntimeStartError,
    RuntimeUnavailable,
)
from code_agent.runtime.local import WindowsLocalRuntime  # noqa: E402
from code_agent.runtime.models import (  # noqa: E402
    CommandSpec,
    StreamName,
    TerminationReason,
)
from code_agent.runtime.tests._local_test_support import (  # noqa: E402
    CompletedProcess,
    LocalRuntimeTestCase,
    patch_process_identity_capture,
)
from code_agent.workspace.errors import PathOutsideWorkspace  # noqa: E402


class WindowsLocalRuntimeTests(LocalRuntimeTestCase):

    async def test_argv_streams_both_pipes_and_reports_exit(self) -> None:
        chunks = []
        code = (
            "import sys;"
            "sys.stdout.buffer.write(b'out');sys.stdout.flush();"
            "sys.stderr.buffer.write(b'err');sys.stderr.flush();"
            "raise SystemExit(7)"
        )

        result = await self.runtime.run(
            CommandSpec(cwd=".", argv=(sys.executable, "-c", code)),
            CancellationToken(),
            chunks.append,
        )

        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"out")
        self.assertEqual(result.stderr, b"err")
        self.assertEqual(result.argv, (sys.executable, "-c", code))
        self.assertEqual(result.cwd, ".")
        self.assertIsNone(result.cancellation_reason)
        self.assertFalse(result.truncated)
        self.assertEqual(
            b"".join(item.data for item in chunks if item.stream is StreamName.STDOUT),
            b"out",
        )
        self.assertEqual(
            b"".join(item.data for item in chunks if item.stream is StreamName.STDERR),
            b"err",
        )

    async def test_cwd_outside_workspace_is_rejected(self) -> None:
        with self.assertRaises(PathOutsideWorkspace):
            await self.runtime.run(
                CommandSpec(cwd=self.root.parent, argv=(sys.executable, "-V")),
                CancellationToken(),
                None,
            )

    async def test_sensitive_and_unapproved_environment_is_not_inherited(self) -> None:
        code = (
            "import os;print('|'.join(str(os.getenv(n)) for n in "
            "('OPENAI_API_KEY','ACCESS_TOKEN','UNAPPROVED')))"
        )
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "host-key", "ACCESS_TOKEN": "host-token"},
        ):
            result = await self.runtime.run(
                CommandSpec(
                    cwd=".",
                    argv=(sys.executable, "-c", code),
                    explicit_env={"UNAPPROVED": "explicit-value"},
                ),
                CancellationToken(),
                None,
            )

        self.assertEqual(result.stdout.strip(), b"None|None|None")

    async def test_approved_explicit_environment_is_visible(self) -> None:
        runtime = WindowsLocalRuntime(self.root, allowed_env_names=("SAFE_FLAG",))
        result = await runtime.run(
            CommandSpec(
                cwd=".",
                argv=(
                    sys.executable,
                    "-c",
                    "import os;print(os.getenv('SAFE_FLAG'))",
                ),
                explicit_env={"safe_flag": "visible"},
            ),
            CancellationToken(),
            None,
        )

        self.assertEqual(result.stdout.strip(), b"visible")

    async def test_combined_output_limit_bounds_capture_and_callback(self) -> None:
        chunks = []
        code = (
            "import sys,time;"
            "sys.stdout.buffer.write(b'a'*4096);sys.stdout.flush();"
            "sys.stderr.buffer.write(b'b'*4096);sys.stderr.flush();time.sleep(5)"
        )
        result = await self.runtime.run(
            CommandSpec(
                cwd=".",
                argv=(sys.executable, "-c", code),
                max_output_bytes=100,
            ),
            CancellationToken(),
            chunks.append,
        )

        self.assertEqual(result.reason, TerminationReason.OUTPUT_LIMIT)
        self.assertTrue(result.truncated)
        self.assertIsNone(result.returncode)
        self.assertLessEqual(len(result.stdout) + len(result.stderr), 100)
        self.assertLessEqual(sum(len(item.data) for item in chunks), 100)
        self.assertTrue(result.truncated_streams)

    async def test_output_limit_records_the_stream_that_lost_bytes(self) -> None:
        code = (
            "import sys;"
            "sys.stdout.buffer.write('中'.encode('utf-8')*100);"
            "sys.stdout.flush()"
        )

        result = await self.runtime.run(
            CommandSpec(
                cwd=".", argv=(sys.executable, "-c", code), max_output_bytes=2
            ),
            CancellationToken(),
            None,
        )

        self.assertEqual(result.reason, TerminationReason.OUTPUT_LIMIT)
        self.assertEqual(result.stdout, "中".encode("utf-8")[:2])
        self.assertEqual(result.truncated_streams, frozenset({StreamName.STDOUT}))

    async def test_spawn_oserror_is_wrapped(self) -> None:
        with patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec",
            side_effect=OSError("cannot start"),
        ):
            with self.assertRaises(RuntimeStartError) as raised:
                await self.runtime.run(
                    CommandSpec(cwd=".", argv=(sys.executable, "-V")),
                    CancellationToken(),
                    None,
                )

        self.assertIsInstance(raised.exception.__cause__, OSError)

if __name__ == "__main__":
    unittest.main()
