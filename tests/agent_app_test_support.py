from __future__ import annotations

import asyncio
import contextlib
import hashlib
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.config.loader import load_runtime_config  # noqa: E402
from code_agent.context.builder import WorkspaceContextBuilder  # noqa: E402
from code_agent.context.compaction import DeterministicCompactor  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.context.repo_map import RepoMapBuilder  # noqa: E402
from code_agent.context.rules import RuleLoader  # noqa: E402
from code_agent.core.cancellation import CancellationError, CancellationToken  # noqa: E402
from code_agent.interfaces.terminal_state import ApprovalBroker  # noqa: E402
from code_agent.policy.engine import ActionPolicy, PolicyConfig  # noqa: E402
from code_agent.policy.models import ApprovalMode  # noqa: E402
from code_agent.runtime.models import CommandResult, TerminationReason  # noqa: E402
from code_agent.verification.python_adapter import PythonVerificationAdapter  # noqa: E402
from code_agent.workspace.edits import WorkspaceEditor  # noqa: E402
from code_agent.workspace.files import WorkspaceFiles  # noqa: E402
from code_agent.workspace.ignore import IgnoreRules  # noqa: E402
from code_agent.workspace.paths import WorkspacePathGuard  # noqa: E402
from code_agent_win import agent_modes  # noqa: E402
from code_agent_win.app import RootActionDispatcher, create_application  # noqa: E402
from tests.test_agent_app_full_stack import FakeModel  # noqa: E402


def _configured_application(root: Path):
    if not (root / ".git").exists() and not (root / "note.py").exists():
        return _isolated_application(root)
    return _workspace_application(root)


def _isolated_application(
    container: Path,
    *,
    approval_mode: str = "full-local",
    allow_sensitive_paths: bool = False,
):
    workspace, product = container / "workspace", container / "state"
    workspace.mkdir()
    config_env = {
        "CHAOS_CONFIG": str(container / "missing.toml"),
        "CHAOS_API": "responses",
        "CHAOS_BASE_URL": "https://api.example.test",
        "CHAOS_MODEL": "test",
        "CHAOS_API_KEY_ENV": "KEY",
        "CHAOS_APPROVAL_MODE": approval_mode,
    }
    if allow_sensitive_paths:
        config_env["CHAOS_ALLOW_SENSITIVE_PATHS"] = "true"
    runtime = load_runtime_config(env=config_env)
    patches = (
        patch.dict("os.environ", {
            "USERPROFILE": str(container / "profile"),
            "LOCALAPPDATA": str(container / "localappdata"),
        }, clear=True),
        patch("code_agent_win.app._model_client", return_value=object()),
        patch("code_agent_win.app._session_path",
              return_value=product / "sessions.sqlite3"),
        patch("code_agent_win.app._product_state_root", return_value=product),
        patch("code_agent_win.app.load_runtime_config", return_value=runtime),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        return create_application(workspace), workspace, product


def _workspace_application(root: Path):
    state = root.parent / f"{root.name}-state"
    state.mkdir(exist_ok=True)
    runtime = load_runtime_config(env={
        "CHAOS_CONFIG": str(root / "missing.toml"),
        "CHAOS_API": "responses",
        "CHAOS_BASE_URL": "https://api.example.test",
        "CHAOS_MODEL": "test",
        "CHAOS_API_KEY_ENV": "KEY",
        "CHAOS_APPROVAL_MODE": "auto",
    })
    mode_env = {
        f"CHAOS_MODE_{mode.value.upper()}_PROFILE": runtime.profile
        for mode in agent_modes.AgentMode
    }
    patches = (
        patch("code_agent_win.app._model_client", side_effect=lambda _: object()),
        patch("code_agent_win.app._session_path", return_value=state / "sessions.sqlite3"),
        patch(
            "code_agent_win.app._workspace_storage_path",
            return_value=state / "managed-workspaces",
        ),
        patch("code_agent_win.app.load_runtime_config", return_value=runtime),
        patch.dict("os.environ", mode_env),
    )
    stack = contextlib.ExitStack()
    for item in patches:
        stack.enter_context(item)
    application = create_application(root)
    application._test_stack = stack
    return application


def _task_context(root: Path) -> WorkspaceContextBuilder:
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    config = ContextConfig(root, root, "System", repo_scan=100)
    return WorkspaceContextBuilder(
        config,
        RuleLoader(guard, files, config),
        RepoMapBuilder(files, config),
        DeterministicCompactor(config),
    )


def _task_dispatcher(root: Path, runtime: object) -> RootActionDispatcher:
    guard = WorkspacePathGuard(root)
    files = WorkspaceFiles(guard, IgnoreRules.from_workspace(root))
    return RootActionDispatcher(
        files,
        WorkspaceEditor(guard),
        ActionPolicy(PolicyConfig(ApprovalMode.AUTO, workspace_root=root)),
        ApprovalBroker(),
        runtime=runtime,  # type: ignore[arg-type]
        verification=PythonVerificationAdapter(root),
    )


def _init_git_source(root: Path) -> None:
    (root / "note.py").write_text("print('source')\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "note.py")
    _git(root, "commit", "-m", "initial")
    (root / "note.py").write_text("print('dirty')\n", encoding="utf-8")


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and ".git" not in path.parts:
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class _RecordingRuntime:
    def __init__(self, returncodes: tuple[int, ...]) -> None:
        self.returncodes = list(returncodes)
        self.commands: list[str] = []

    async def run(self, spec: object, cancellation: object, sink: object) -> CommandResult:
        command = " ".join(getattr(spec, "argv") or ())
        self.commands.append(command)
        returncode = self.returncodes.pop(0)
        return CommandResult(
            argv=("powershell",), display_command=command, returncode=returncode,
            reason=TerminationReason.EXITED, stdout=b"",
            stderr=b"test failure" if returncode else b"",
            duration_s=0, truncated=False, cwd=".",
        )


class _BlockingRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.commands: list[str] = []

    async def run(
        self, spec: object, cancellation: CancellationToken, sink: object,
    ) -> CommandResult:
        command = " ".join(getattr(spec, "argv") or ())
        self.commands.append(command)
        self.started.set()
        await cancellation.wait_async()
        raise CancellationError(cancellation.reason)


async def _collect_events(events: AsyncIterator[object]) -> list[object]:
    return [event async for event in events]
