"""Offline official-SDK server; all effects remain in its explicit temporary root."""
import asyncio
import os
import sys
import time
import threading
from pathlib import Path

root = Path(sys.argv[1]).resolve()
assert root.is_dir() and root.name.startswith("s13-mcp-")
(root / "pid").write_text(str(os.getpid()), encoding="utf-8")
mode = sys.argv[2]
if mode == "handshake":
    time.sleep(120)
    raise SystemExit

from mcp.server.fastmcp import FastMCP

server = FastMCP("s13-offline")

if mode == "idle_exit":
    threading.Timer(1.5, lambda: os._exit(4)).start()

if mode == "discovery":
    @server._mcp_server.list_tools()
    async def unresponsive_tools():
        await asyncio.sleep(120)
        return []


@server.tool()
async def operation(sequence: int = 0) -> str:
    """A fixed fixture operation, not an arbitrary filesystem or shell tool."""
    with (root / "calls").open("a", encoding="utf-8") as stream:
        stream.write("call\n")
    if mode == "call":
        await asyncio.sleep(120)
    if mode == "queue" and sequence == 0:
        while not (root / "release").exists(): await asyncio.sleep(0.01)
    if mode == "exit":
        os._exit(3)
    return "fixture success"


server.run(transport="stdio")
if mode == "close":
    time.sleep(120)
