from __future__ import annotations

from collections.abc import Sequence

from .remote.pairing import PairingStore
from .remote.server import create_host_app


async def serve_host(application: object, arguments: Sequence[str]) -> int:
    bind, port = _parse(arguments)
    bind = bind or "127.0.0.1"
    app, pairing = create_host_app(application)
    token = pairing.issue_token()
    print(f"Chaos Agent Host listening on http://{bind}:{port}")
    print(f"Pairing token: {token}")
    if bind == "127.0.0.1":
        print("Localhost-only mode; use --lan for same-Wi-Fi phone access.")
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError("host requires uvicorn; install the chaos-agent host dependencies") from error
    config = uvicorn.Config(app, host=bind, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
    return 0


def revoke_device() -> int:
    PairingStore().revoke()
    print("Chaos Agent Host device revoked.")
    print("Restart Host to display a new single-use pairing token.")
    return 0


def _parse(arguments: Sequence[str]) -> tuple[str | None, int]:
    bind = None
    port = 8787
    seen: set[str] = set()
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option not in {"--bind", "--port", "--lan"} or option in seen:
            raise ValueError("host accepts --lan, --bind <address>, --port <1..65535>, or revoke-device")
        if option in {"--bind", "--lan"} and seen & {"--bind", "--lan"}:
            raise ValueError("host --lan cannot be combined with --bind")
        seen.add(option)
        if option != "--lan" and (index + 1 >= len(arguments) or not arguments[index + 1] or arguments[index + 1].startswith("--")):
            raise ValueError("host option requires a value")
        if option == "--bind":
            value = arguments[index + 1]
            bind = value
            index += 2
        elif option == "--port":
            value = arguments[index + 1]
            port = int(value)
            if not 1 <= port <= 65535:
                raise ValueError("host port must be between 1 and 65535")
            index += 2
        elif option == "--lan":
            bind = "0.0.0.0"
            index += 1
        else:
            raise ValueError(f"unknown host option: {option}")
    return bind, port
