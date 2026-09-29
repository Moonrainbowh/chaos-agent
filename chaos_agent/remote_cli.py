from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence

from .remote.server import create_host_app


async def serve_host(application: object, arguments: Sequence[str]) -> int:
    bind, port = _parse(arguments)
    if bind is None:
        bind = _tailscale_address() or "127.0.0.1"
    app, pairing = create_host_app(application)
    token = pairing.issue_token()
    print(f"Chaos Agent Host listening on http://{bind}:{port}")
    print(f"Pairing token: {token}")
    if bind == "127.0.0.1":
        print("Tailscale address not found; use --bind <tailscale-ip> for phone access.")
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError("host requires uvicorn; install the chaos-agent host dependencies") from error
    config = uvicorn.Config(app, host=bind, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
    return 0


def _parse(arguments: Sequence[str]) -> tuple[str | None, int]:
    bind = None
    port = 8787
    index = 0
    while index < len(arguments):
        option = arguments[index]
        value = arguments[index + 1]
        if option == "--bind":
            bind = value
        elif option == "--port":
            port = int(value)
        index += 2
    return bind, port


def _tailscale_address() -> str | None:
    if shutil.which("tailscale") is None:
        return None
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        value = line.strip()
        if value:
            return value
    return None
