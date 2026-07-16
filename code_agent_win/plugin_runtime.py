from __future__ import annotations

import json
import os
from pathlib import Path

from code_agent.core.models import ToolDefinition
from code_agent.orchestration.modes import ModeRegistry
from code_agent.plugins.manifest import ManifestError, PluginTrustStore, load_manifest
from code_agent.plugins.models import PluginRisk
from code_agent.plugins.registry import PluginHost, PluginRegistryBuilder


class PluginToolBridge:
    def __init__(self, host: PluginHost) -> None:
        self._host = host

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                item.qualified_id,
                item.value.description,
                item.value.input_schema,
            )
            for item in self._host.contributions("tool")
        )

    def targets(self) -> dict[str, str]:
        return {
            item.qualified_id: item.value.target
            for item in self._host.contributions("tool")
        }

    def risk_map(self) -> dict[str, str]:
        return {
            item.qualified_id: item.value.risk.value
            for item in self._host.contributions("tool")
        }


def load_plugins(
    workspace_root: Path,
    modes: ModeRegistry,
    *,
    host_actions: tuple[str, ...],
    host_risks: dict[str, PluginRisk],
    mcp_tools: tuple[str, ...] = (),
    controllers: tuple[str, ...] = (),
) -> tuple[PluginHost, tuple[str, ...]]:
    trust = PluginTrustStore(_read_trust_store())
    manifests = []
    errors: list[str] = []
    for path in _manifest_paths(workspace_root):
        try:
            manifest = load_manifest(path, trust, host_api="1")
            manifests.append(manifest)
            if not manifest.enabled:
                state = "disabled" if manifest.trusted else "untrusted"
                errors.append(f"{manifest.identifier}: inactive ({state})")
        except (OSError, ManifestError) as error:
            errors.append(f"{path}: {type(error).__name__}")
    builder = PluginRegistryBuilder(
        modes,
        host_actions=host_actions,
        host_action_risks=host_risks,
        mcp_tools=mcp_tools,
        controllers=controllers,
    )
    snapshot = builder.build(manifests)
    return PluginHost(snapshot), tuple(errors) + snapshot.errors


def _manifest_paths(workspace_root: Path) -> tuple[Path, ...]:
    local = _local_data_root() / "plugins"
    workspace = workspace_root / ".chaos-agent" / "plugins"
    paths: list[Path] = []
    for root in (local, workspace):
        if not root.is_dir() or root.is_symlink():
            continue
        paths.extend(
            child / "plugin.json"
            for child in sorted(root.iterdir())
            if child.is_dir() and not child.is_symlink() and (child / "plugin.json").is_file()
        )
    return tuple(paths)


def _read_trust_store() -> dict[str, str]:
    path = _local_data_root() / "plugin-trust.json"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024:
        return {}
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _local_data_root() -> Path:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "chaos-agent"
