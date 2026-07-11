from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from code_agent.workspace.errors import FileTooLargeError, WorkspaceError
from code_agent.workspace.files import WorkspaceFiles
from code_agent.workspace.paths import PathInput, WorkspacePathGuard

from .errors import ContextError, RuleLimitError
from .models import ContextConfig, ProjectRule
from .tokens import estimate_tokens


class RuleLoader:
    """Load the deterministic rule chain for one guarded working directory."""

    def __init__(
        self,
        guard: WorkspacePathGuard,
        files: WorkspaceFiles,
        config: ContextConfig,
    ) -> None:
        if not isinstance(guard, WorkspacePathGuard):
            raise TypeError("guard must be a WorkspacePathGuard")
        if not isinstance(files, WorkspaceFiles):
            raise TypeError("files must be WorkspaceFiles")
        if not isinstance(config, ContextConfig):
            raise TypeError("config must be a ContextConfig")
        if files.guard is not guard:
            raise ValueError("files and rule loader must share the same guard")
        if guard.root != config.workspace_root:
            raise ValueError("guard root must match config.workspace_root")
        self.guard = guard
        self.files = files
        self.config = config

    def load(self, cwd: PathInput | None = None) -> tuple[ProjectRule, ...]:
        """Load root, root extension, then cwd-chain rules in priority order."""
        working_directory = self._validate_cwd(
            self.config.cwd if cwd is None else cwd
        )
        candidates = [("AGENTS.md", 0)]
        candidates.extend(self._root_extensions())
        candidates.extend(self._ancestor_rules(working_directory))

        rules: list[ProjectRule] = []
        seen: set[str] = set()
        total_bytes = 0
        for path, depth in candidates:
            if path in seen:
                continue
            seen.add(path)
            loaded = self._read_optional(path)
            if loaded is None:
                continue
            byte_count = len(loaded.encode("utf-8"))
            total_bytes += byte_count
            if total_bytes > self.config.max_rules_total:
                raise RuleLimitError(
                    "project rules exceed the configured total byte limit"
                )
            rules.append(ProjectRule(path, loaded, depth))
        return tuple(rules)

    def render(self, rules: Sequence[ProjectRule]) -> str:
        """Render explicit, path-labelled boundaries for loaded rule content."""
        checked = tuple(rules)
        if not all(isinstance(rule, ProjectRule) for rule in checked):
            raise TypeError("rules must contain only ProjectRule values")
        if not checked:
            return ""
        rendered = ["Project rules are ordered from highest to lowest priority."]
        for rule in checked:
            path = json.dumps(rule.path, ensure_ascii=True)
            rendered.extend(
                (
                    f"[PROJECT_RULE path={path} depth={rule.scope_depth}]",
                    rule.content,
                    "[/PROJECT_RULE]",
                )
            )
        text = "\n".join(rendered)
        token_limit = self.config.prompt_budget.max_rule_tokens
        if estimate_tokens(text) > token_limit:
            raise RuleLimitError(f"project rules exceed {token_limit:,} tokens")
        return text

    def _validate_cwd(self, cwd: PathInput) -> Path:
        try:
            resolved = self.guard.resolve(cwd)
        except (OSError, WorkspaceError) as error:
            raise ContextError("cwd is outside the guarded workspace") from error
        if not resolved.is_dir():
            raise ContextError("cwd must be an existing workspace directory")
        return resolved

    def _root_extensions(self) -> list[tuple[str, int]]:
        try:
            paths = self.files.list_files(
                max_entries=self.config.repo_scan,
                max_scanned_entries=max(1_000, self.config.repo_scan * 20),
            )
        except (OSError, WorkspaceError) as error:
            raise ContextError("cannot discover root rule extensions") from error
        extensions = [
            path
            for path in paths
            if "/" not in path
            and path.startswith("AGENTS.")
            and path.endswith(".md")
        ]
        return [(path, 0) for path in sorted(extensions)]

    def _ancestor_rules(self, cwd: Path) -> list[tuple[str, int]]:
        relative = cwd.relative_to(self.guard.root)
        directory = Path()
        rules: list[tuple[str, int]] = []
        for depth, part in enumerate(relative.parts, start=1):
            directory /= part
            rules.append(((directory / "AGENTS.md").as_posix(), depth))
        return rules

    def _read_optional(self, path: str) -> str | None:
        try:
            resolved = self.guard.resolve(path)
            if not resolved.exists():
                return None
            document = self.files.read_text(
                path, max_bytes=self.config.max_rule_bytes
            )
        except FileTooLargeError as error:
            raise RuleLimitError(
                f"project rule exceeds {self.config.max_rule_bytes} bytes: {path}"
            ) from error
        except (OSError, WorkspaceError) as error:
            raise ContextError(f"cannot read project rule: {path}") from error
        return document.text
