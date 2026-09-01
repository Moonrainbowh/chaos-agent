from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Optional

from code_agent.core.cancellation import CancellationToken
from code_agent.policy.environment import sanitize_environment

from .errors import RuntimeUnavailable
from .local import OutputCallback, WindowsLocalRuntime
from .models import CommandResult, CommandSpec, RuntimeKind, ShellDialect


class DockerRuntime:
    """Run commands in an explicitly selected, already-present Docker image."""

    kind = RuntimeKind.DOCKER

    def __init__(
        self,
        root: os.PathLike[str] | str,
        image: str,
        allowed_env_names: Iterable[str] = (),
        *,
        network_enabled: bool = False,
    ) -> None:
        self._validate_image(image)
        if isinstance(allowed_env_names, (str, bytes)):
            raise TypeError("allowed_env_names must be an iterable of names")
        if not isinstance(network_enabled, bool):
            raise TypeError("network_enabled must be a bool")
        self.image = image
        self.network_enabled = network_enabled
        self._allowed_env_names = tuple(allowed_env_names)
        self._executor = WindowsLocalRuntime(root, self._allowed_env_names)

    @property
    def root(self) -> Path:
        return self._executor.root

    def is_available(self) -> bool:
        return shutil.which("docker") is not None

    async def run(
        self,
        spec: CommandSpec,
        cancellation: CancellationToken,
        on_output: Optional[OutputCallback],
    ) -> CommandResult:
        if not isinstance(spec, CommandSpec):
            raise TypeError("spec must be a CommandSpec")
        host_cwd = self._executor._guard.resolve(spec.cwd)
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeUnavailable("Docker executable was not found")

        environment = sanitize_environment(
            os.environ, self._allowed_env_names, spec.explicit_env
        )
        relative = host_cwd.relative_to(self.root)
        container_cwd = PurePosixPath("/workspace", *relative.parts).as_posix()
        arguments = [docker, "run", "--rm", "--pull=never"]
        if not self.network_enabled:
            arguments.extend(("--network", "none"))
        arguments.extend(
            (
                "--mount",
                f"type=bind,source={self.root},target=/workspace",
                "--workdir",
                container_cwd,
            )
        )

        approved = {name.casefold() for name in self._allowed_env_names}
        for name in environment:
            if name.casefold() in approved:
                arguments.extend(("--env", name))
        arguments.append(self.image)
        if spec.argv is not None:
            arguments.extend(spec.argv)
        elif spec.shell_script is not None:
            if spec.shell_script.dialect is not ShellDialect.POSIX_SH:
                raise RuntimeUnavailable(
                    "Docker runtime shell scripts require the posix_sh dialect"
                )
            arguments.extend(("/bin/sh", "-lc", spec.shell_script.text))
        else:
            assert spec.powershell_script is not None
            arguments.extend(("/bin/sh", "-lc", spec.powershell_script))

        wrapper = CommandSpec(
            cwd=host_cwd,
            argv=tuple(arguments),
            timeout_s=spec.timeout_s,
            max_output_bytes=spec.max_output_bytes,
            explicit_env=spec.explicit_env,
        )
        return await self._executor.run(wrapper, cancellation, on_output)

    @staticmethod
    def _validate_image(image: object) -> None:
        if not isinstance(image, str):
            raise TypeError("image must be a string")
        if (
            not image
            or image.startswith("-")
            or any(character.isspace() or ord(character) < 32 for character in image)
        ):
            raise ValueError("image must be a non-blank Docker image reference")
