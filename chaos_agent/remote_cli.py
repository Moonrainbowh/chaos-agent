from __future__ import annotations

from collections.abc import Sequence

from .remote.pairing import PairingStore
from .remote.server import create_host_app
from .remote.transport import parse_host_options


async def serve_host(application: object, arguments: Sequence[str]) -> int:
    transport = parse_host_options(arguments)
    bind, port = transport.bind, transport.port
    app, pairing = create_host_app(application)
    token = pairing.issue_token()
    print(f"Chaos Agent Host listening on {transport.scheme}://{bind}:{port}")
    print(f"Pairing token: {token}")
    if bind == "127.0.0.1":
        print("Localhost-only backend; use a protected HTTPS/WSS reverse-proxy entry for phones.")
    if transport.insecure_lan_debug:
        print("Explicit private-interface HTTP/WS debug mode; credentials are not encrypted on this link.")
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError("host requires uvicorn; install the chaos-agent host dependencies") from error
    config = uvicorn.Config(app, host=bind, port=port, log_level="info",
                            ssl_certfile=transport.certificate, ssl_keyfile=transport.private_key)
    server = uvicorn.Server(config)
    await server.serve()
    return 0


def revoke_device() -> int:
    PairingStore().revoke()
    print("Chaos Agent Host device revoked.")
    print("Restart Host to display a new single-use pairing token.")
    return 0


def _parse(arguments: Sequence[str]) -> tuple[str | None, int]:
    """Compatibility tuple; production uses the complete validated transport."""
    transport = parse_host_options(arguments)
    return (transport.bind if "--bind" in arguments or "--lan" in arguments else None,
            transport.port)
