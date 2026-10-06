"""Validate explicit Host transport choices before constructing runtime state."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import ssl
from collections.abc import Sequence


@dataclass(frozen=True)
class HostTransport:
    bind: str = "127.0.0.1"
    port: int = 8787
    certificate: str | None = None
    private_key: str | None = None
    insecure_lan_debug: bool = False

    @property
    def scheme(self) -> str:
        return "https" if self.certificate else "http"


def parse_host_options(arguments: Sequence[str]) -> HostTransport:
    values: dict[str, str | bool] = {}
    flags = {"--lan", "--insecure-lan-debug"}
    options = flags | {"--bind", "--port", "--tls-cert", "--tls-key"}
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option not in options or option in values:
            raise ValueError("unknown or repeated Host option")
        if option in flags:
            values[option] = True
            index += 1
        else:
            if index + 1 >= len(arguments) or not arguments[index + 1] or arguments[index + 1].startswith("--"):
                raise ValueError("Host option requires a value")
            values[option] = arguments[index + 1]
            index += 2
    if "--lan" in values and "--bind" in values:
        raise ValueError("--lan cannot be combined with --bind")
    bind = str(values.get("--bind", "0.0.0.0" if values.get("--lan") else "127.0.0.1"))
    try:
        port = int(str(values.get("--port", 8787)))
    except ValueError:
        raise ValueError("Host port must be between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise ValueError("Host port must be between 1 and 65535")
    try:
        address = ipaddress.ip_address(bind)
    except ValueError:
        if bind != "localhost":
            raise ValueError("Host bind must be an IP address or localhost") from None
        address = ipaddress.ip_address("127.0.0.1")
    certificate, private_key = values.get("--tls-cert"), values.get("--tls-key")
    if bool(certificate) != bool(private_key):
        raise ValueError("--tls-cert and --tls-key must be provided together")
    debug = bool(values.get("--insecure-lan-debug"))
    if certificate:
        if debug:
            raise ValueError("TLS and insecure LAN debug cannot be combined")
        _validate_certificate(str(certificate), str(private_key))
    elif not address.is_loopback:
        # Wildcard binds include every interface; a debug option is not a firewall.
        networks = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                    ipaddress.ip_network("192.168.0.0/16"))
        private_interface = address.version == 4 and any(address in network for network in networks)
        if not debug or not private_interface:
            raise ValueError("remote Host requires TLS; restricted plaintext debug requires --bind <private IPv4> --insecure-lan-debug")
    elif debug:
        raise ValueError("insecure LAN debug requires a specific private IPv4 interface")
    return HostTransport(bind, port, str(certificate) if certificate else None,
                         str(private_key) if private_key else None, debug)


def _validate_certificate(certificate: str, private_key: str) -> None:
    if not Path(certificate).is_file() or not Path(private_key).is_file():
        raise ValueError("TLS certificate and key files must exist")
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private_key)
    except (OSError, ssl.SSLError, ValueError):
        raise ValueError("TLS certificate/key cannot be loaded; verify their pairing and format") from None
