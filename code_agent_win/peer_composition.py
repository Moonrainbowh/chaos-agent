from __future__ import annotations

import asyncio
import os
from pathlib import Path

import psutil

from code_agent.peers.models import PeerInboundPolicy, PeerSessionStatus
from code_agent.peers.service import PeerMessagingService
from code_agent_win.peer_runtime import PeerRuntime
from code_agent_win.peer_tools import PeerToolAdapter


def compose_peers(
    sessions: object, root: Path, permission_mode: str
) -> tuple[list[object], asyncio.Lock, object, object, PeerRuntime]:
    process = psutil.Process(os.getpid())
    tui_ref: list[object] = []
    activity_lock = asyncio.Lock()
    service = PeerMessagingService(
        sessions,
        owner_pid=process.pid,
        owner_create_time=process.create_time(),
        workspace_root=str(root),
        permission_mode=permission_mode,
        name=(root.name or "chaos-agent")[:80],
        inbound_policy=PeerInboundPolicy.AUTO,
        status=PeerSessionStatus.IDLE,
    )
    tools = PeerToolAdapter(service)
    runtime = PeerRuntime(
        service,
        root,
        tui_ref,
        permission_mode=permission_mode,
        activity_lock=activity_lock,
    )
    return tui_ref, activity_lock, service, tools, runtime
