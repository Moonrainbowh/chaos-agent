from __future__ import annotations

from typing import Any

from .terminal_display import DisplayKind


async def handle_mcp_command(app: Any, instruction: str | None) -> bool:
    if app.mcp is None:
        app._append(DisplayKind.ERROR, "MCP is unavailable")
        return False
    action, _, name = (instruction or "列表").partition(" ")
    try:
        if action in {"列表", "list", "状态", "status"}:
            servers = app.mcp.status(name or None)
            app._append(DisplayKind.METADATA, " | ".join(f"{server.name}:{'enabled' if server.enabled else 'disabled'}" for server in servers))
        elif action in {"工具", "tools"}:
            definitions = app.mcp.definitions()
            if name:
                definitions = tuple(
                    item for item in definitions
                    if item.name.startswith(f"mcp.{name}.")
                )
            app._append(
                DisplayKind.METADATA,
                " | ".join(item.name for item in definitions) or "No MCP tools",
            )
        elif action in {"诊断", "diagnose"} and name:
            app._append(DisplayKind.METADATA, str(app.mcp.diagnose(name)))
        elif action in {"启用", "enable"} and name:
            tools = await app.mcp.enable(name); app._append(DisplayKind.METADATA, f"MCP enabled: {name} ({len(tools)} tools)")
        elif action in {"禁用", "disable"} and name:
            await app.mcp.disable(name); app._append(DisplayKind.METADATA, f"MCP disabled: {name}")
        elif action in {"重启", "restart"} and name:
            tools = await app.mcp.restart(name); app._append(DisplayKind.METADATA, f"MCP restarted: {name} ({len(tools)} tools)")
        else:
            app._append(DisplayKind.ERROR, "MCP expects list, status, enable, disable, restart, or diagnose")
            return False
    except (KeyError, PermissionError, RuntimeError, ValueError) as error:
        app._append(DisplayKind.ERROR, type(error).__name__)
        return False
    return True
