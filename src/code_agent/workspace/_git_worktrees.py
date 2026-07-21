from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .git import GitCommandError, GitWorkspace, _decode


@dataclass(frozen=True)
class GitWorktreeEntry:
    root: Path
    prunable: bool


class FixedGitWorktreeCommands:
    """Fixed, bounded Git commands used only by WorktreeManager."""

    def __init__(self, git: GitWorkspace) -> None:
        self._git = git

    def common_dir(self) -> Path:
        result = self._run(
            "common_dir", ("rev-parse", "--path-format=absolute", "--git-common-dir")
        )
        raw = _decode(result.stdout).strip()
        if not raw:
            raise GitCommandError(
                "common_dir", result.argv, result.returncode, "", "git returned no common dir"
            )
        return Path(raw).resolve(strict=True)

    def head_commit(self) -> str:
        return self._text("head_commit", ("rev-parse", "--verify", "HEAD"))

    def current_branch(self) -> str:
        return self._text("current_branch", ("branch", "--show-current"))

    def branch_exists(self, branch: str) -> bool:
        result = self._git._invoke(
            "branch_exists", ("show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
        )
        if result.returncode == 1:
            return False
        self._git._require_success("branch_exists", result)
        return True

    def branch_tip(self, branch: str) -> str | None:
        result = self._git._invoke(
            "branch_tip", ("rev-parse", "--verify", f"refs/heads/{branch}")
        )
        if result.returncode in (1, 128):
            return None
        self._git._require_success("branch_tip", result)
        return _decode(result.stdout).strip()

    def add(self, branch: str, target: Path, head: str) -> None:
        self._run("worktree_add", ("worktree", "add", "-b", branch, str(target), head))

    def remove(self, target: Path) -> None:
        self._run("worktree_remove", ("worktree", "remove", str(target)))

    def force_remove(self, target: Path) -> None:
        self._run("worktree_remove", ("worktree", "remove", "--force", str(target)))

    def delete_branch(self, branch: str) -> None:
        self._run("branch_delete", ("branch", "-D", branch))

    def entries(self) -> tuple[GitWorktreeEntry, ...]:
        result = self._run("worktree_list", ("worktree", "list", "--porcelain", "-z"))
        return _parse_entries(result.stdout)

    def prune(self) -> None:
        self._run("worktree_prune", ("worktree", "prune", "--expire", "now"))

    def _text(self, operation: str, arguments: tuple[str, ...]) -> str:
        return _decode(self._run(operation, arguments).stdout).strip()

    def _run(self, operation: str, arguments: tuple[str, ...]):
        result = self._git._invoke(operation, arguments)
        self._git._require_success(operation, result)
        return result


def _parse_entries(output: bytes) -> tuple[GitWorktreeEntry, ...]:
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GitCommandError(
            "worktree_list", (), None, "invalid UTF-8 path", "git returned an undecodable worktree path"
        ) from error
    entries: list[GitWorktreeEntry] = []
    for record in text.split("\0\0"):
        fields = tuple(field for field in record.split("\0") if field)
        roots = [field[9:] for field in fields if field.startswith("worktree ")]
        if len(roots) == 1:
            entries.append(
                GitWorktreeEntry(
                    Path(roots[0]).resolve(strict=False),
                    any(field == "prunable" or field.startswith("prunable ") for field in fields),
                )
            )
    return tuple(entries)
