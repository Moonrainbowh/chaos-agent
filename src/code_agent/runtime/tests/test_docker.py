from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.runtime.docker import DockerRuntime  # noqa: E402
from code_agent.runtime.errors import RuntimeUnavailable  # noqa: E402
from code_agent.runtime.models import (  # noqa: E402
    CommandSpec,
    RuntimeKind,
    TerminationReason,
)
from code_agent.runtime.tests._local_test_support import (  # noqa: E402
    patch_process_identity_capture,
)


class CompletedDockerProcess:
    def __init__(self) -> None:
        self.pid = 5432
        self.returncode: int | None = None
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


class DockerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "nested").mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_image_is_validated_without_contacting_docker(self) -> None:
        for image in ("", " ", "--privileged", "repo/image latest", "bad\0image"):
            with self.subTest(image=image):
                with self.assertRaises((TypeError, ValueError)):
                    DockerRuntime(self.root, image)

    def test_is_available_only_checks_for_docker_executable(self) -> None:
        runtime = DockerRuntime(self.root, "python:3.12")
        with patch(
            "code_agent.runtime.docker.shutil.which",
            side_effect=("C:\\docker.exe", None),
        ) as which:
            self.assertTrue(runtime.is_available())
            self.assertFalse(runtime.is_available())

        self.assertEqual(which.call_args_list[0].args, ("docker",))
        self.assertEqual(which.call_count, 2)

    async def test_argv_uses_fixed_network_mount_and_workdir(self) -> None:
        process = CompletedDockerProcess()

        async def spawn(*args: object, **kwargs: object) -> CompletedDockerProcess:
            return process

        runtime = DockerRuntime(self.root, "python:3.12")
        with patch(
            "code_agent.runtime.docker.shutil.which", return_value="C:\\docker.exe"
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec", side_effect=spawn
        ) as create, patch_process_identity_capture():
            result = await runtime.run(
                CommandSpec(cwd="nested", argv=("python", "-c", "print('ok')")),
                CancellationToken(),
                None,
            )

        args = create.call_args.args
        self.assertEqual(
            args[:6],
            (
                "C:\\docker.exe",
                "run",
                "--rm",
                "--pull=never",
                "--network",
                "none",
            ),
        )
        self.assertIn("--mount", args)
        self.assertIn(f"type=bind,source={self.root},target=/workspace", args)
        self.assertIn("--workdir", args)
        self.assertIn("/workspace/nested", args)
        image_at = args.index("python:3.12")
        self.assertEqual(args[image_at + 1 :], ("python", "-c", "print('ok')"))
        self.assertEqual(result.reason, TerminationReason.EXITED)
        self.assertEqual(result.cwd, "nested")
        self.assertIsNone(result.cancellation_reason)
        self.assertEqual(runtime.kind, RuntimeKind.DOCKER)

    async def test_environment_uses_names_and_never_places_values_in_argv(self) -> None:
        process = CompletedDockerProcess()

        async def spawn(*args: object, **kwargs: object) -> CompletedDockerProcess:
            return process

        runtime = DockerRuntime(
            self.root, "python:3.12", allowed_env_names=("APP_FLAG",)
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "host-secret"}), patch(
            "code_agent.runtime.docker.shutil.which", return_value="docker.exe"
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec", side_effect=spawn
        ) as create, patch_process_identity_capture():
            await runtime.run(
                CommandSpec(
                    cwd=".",
                    argv=("python", "-V"),
                    explicit_env={
                        "app_flag": "approved-value",
                        "UNAPPROVED": "unapproved-value",
                    },
                ),
                CancellationToken(),
                None,
            )

        args = create.call_args.args
        child_env = create.call_args.kwargs["env"]
        env_at = args.index("--env")
        self.assertEqual(args[env_at + 1], "APP_FLAG")
        self.assertNotIn("approved-value", args)
        self.assertNotIn("unapproved-value", args)
        self.assertNotIn("host-secret", args)
        self.assertEqual(child_env["APP_FLAG"], "approved-value")
        self.assertNotIn("UNAPPROVED", child_env)
        self.assertNotIn("OPENAI_API_KEY", child_env)

    async def test_script_uses_bin_sh_lc(self) -> None:
        process = CompletedDockerProcess()

        async def spawn(*args: object, **kwargs: object) -> CompletedDockerProcess:
            return process

        with patch(
            "code_agent.runtime.docker.shutil.which", return_value="docker.exe"
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec", side_effect=spawn
        ) as create, patch_process_identity_capture():
            await DockerRuntime(self.root, "alpine:3").run(
                CommandSpec(cwd=".", powershell_script="printf ready"),
                CancellationToken(),
                None,
            )

        args = create.call_args.args
        image_at = args.index("alpine:3")
        self.assertEqual(args[image_at + 1 :], ("/bin/sh", "-lc", "printf ready"))

    async def test_missing_docker_raises_without_starting_or_pulling(self) -> None:
        with patch(
            "code_agent.runtime.docker.shutil.which", return_value=None
        ), patch(
            "code_agent.runtime.local.asyncio.create_subprocess_exec"
        ) as create:
            with self.assertRaises(RuntimeUnavailable):
                await DockerRuntime(self.root, "python:3.12").run(
                    CommandSpec(cwd=".", argv=("python", "-V")),
                    CancellationToken(),
                    None,
                )

        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
