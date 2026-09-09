"""Bounded loopback OAuth callback, adapted from uri-agent (MIT)."""
from __future__ import annotations

import asyncio
import secrets
from urllib.parse import parse_qs, urlsplit

from .flows_common import show
from .models import AuthError


def parse_code(value: str, state: str | None, *, pasted: bool = False) -> str:
    """Validate state before accepting callback; manual bare codes are explicit input."""
    value = value.strip()
    if pasted and value.startswith("code="):
        value = "/?" + value
    if "://" in value or value.startswith("/"):
        query = parse_qs(urlsplit(value).query)
        received = query.get("state", [""])[0]
        if state is not None and not secrets.compare_digest(received, state):
            raise AuthError("OAuth callback state mismatch")
        if "error" in query:
            raise AuthError("OAuth authorization denied")
        code = query.get("code", [""])[0]
    elif pasted:
        code, separator, received = value.partition("#")
        if separator and state is not None and not secrets.compare_digest(received, state):
            raise AuthError("OAuth callback state mismatch")
    else:
        raise AuthError("Invalid OAuth callback")
    if not code or len(code) > 8192 or any(c.isspace() for c in code):
        raise AuthError("OAuth authorization code missing or invalid")
    return code


class BrowserCallback:
    """Bind before opening browser; always close listener on cancellation."""

    def __init__(self, redirect: str, state: str | None):
        self.redirect, self.state = redirect, state
        self.server = None
        self.result = None

    async def __aenter__(self):
        self.result = asyncio.get_running_loop().create_future()
        parts = urlsplit(self.redirect)
        try:
            self.server = await asyncio.start_server(self._handle, "127.0.0.1", parts.port)
        except OSError:
            return self
        if parts.port == 0:
            port = self.server.sockets[0].getsockname()[1]
            self.redirect = f"http://127.0.0.1:{port}{parts.path}"
        return self

    async def __aexit__(self, *_):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        if self.result is not None and not self.result.done():
            self.result.cancel()

    async def _handle(self, reader, writer):
        status, body = "400 Bad Request", "Invalid callback."
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            method, target, _ = line.decode("ascii").strip().split(" ", 2)
            if method != "GET" or urlsplit(target).path != urlsplit(self.redirect).path:
                raise AuthError("Unexpected callback path")
            code = parse_code(target, self.state)
            if not self.result.done():
                self.result.set_result(code)
            status, body = "200 OK", "Authorization response received. Return to the terminal to finish sign-in."
        except AuthError as error:
            if str(error) == "OAuth authorization denied" and not self.result.done():
                self.result.set_exception(error)
        except (ValueError, UnicodeError, asyncio.TimeoutError):
            pass
        finally:
            payload = body.encode("utf-8")
            writer.write((f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
                          f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode() + payload)
            try:
                await writer.drain()
            except ConnectionError:
                pass
            writer.close()
            await writer.wait_closed()

    async def receive(self, url: str, display, read_input=None) -> str:
        await show(url, display)
        if self.server is not None:
            return await self.result
        if read_input is None:
            raise AuthError("OAuth callback port unavailable; provide manual input or device_code")
        return parse_code(await read_input("请粘贴浏览器跳转后的完整 URL 或授权码："),
                          self.state, pasted=True)
