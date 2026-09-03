from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from acp import run_agent

from code_agent.acp import ChaosAcpAgent


async def serve_acp(application: object) -> None:
    """Serve one fully composed application over ACP stdio."""
    sessions = getattr(application, "sessions", None)
    root = getattr(application, "workspace_root", None)
    controller = getattr(application, "controller", None)
    if sessions is None or controller is None or not isinstance(root, Path):
        raise RuntimeError("application does not expose ACP integration dependencies")
    dispatcher = getattr(application, "dispatcher", None)
    if dispatcher is not None and hasattr(dispatcher, "interactive"):
        dispatcher.interactive = False
    try:
        current_version = package_version("chaos-agent")
    except PackageNotFoundError:
        current_version = "unknown"
    await run_agent(
        ChaosAcpAgent(
            controller,
            sessions,
            root,
            version=current_version,
            permission_scope=getattr(dispatcher, "permission_scope", None),
        )
    )


__all__ = ["serve_acp"]
