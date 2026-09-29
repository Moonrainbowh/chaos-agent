from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .pairing import PairingStore
from .task_controller import RemoteTaskController


def create_host_app(
    application: object, *, pairing: PairingStore | None = None
) -> tuple[Starlette, PairingStore]:
    pairing = pairing or PairingStore()
    tasks = RemoteTaskController(application)
    static_path = Path(__file__).with_name("static") / "index.html"

    async def index(request: object) -> HTMLResponse:
        return HTMLResponse(static_path.read_text(encoding="utf-8"))

    async def pair(request: Any) -> JSONResponse:
        body = await request.json()
        try:
            credential = pairing.pair(body.get("token", ""))
        except PermissionError as error:
            return JSONResponse({"error": str(error)}, status_code=401)
        return JSONResponse({"device_credential": credential})

    async def status(request: Any) -> JSONResponse:
        if not _authorized(request.headers.get("authorization"), pairing):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse({"host": "online", **await tasks.status()})

    async def message(request: Any) -> JSONResponse:
        if not _authorized(request.headers.get("authorization"), pairing):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 65536:
            return JSONResponse(
                {"error": "prompt must be 1..65536 characters"}, status_code=400
            )
        try:
            return JSONResponse(await tasks.start(prompt))
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, status_code=409)

    async def stop(request: Any) -> JSONResponse:
        if not _authorized(request.headers.get("authorization"), pairing):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            await tasks.stop(request.path_params["task_id"])
        except KeyError:
            return JSONResponse({"error": "unknown task"}, status_code=404)
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        return JSONResponse({"status": "stopped"})

    async def events(websocket: WebSocket) -> None:
        try:
            since = int(websocket.query_params.get("since", "0"))
        except ValueError:
            await websocket.close(code=4400)
            return
        await websocket.accept()
        try:
            auth_message = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            credential = auth_message.removeprefix("auth:") if auth_message.startswith("auth:") else None
            if not pairing.authenticate(credential):
                await websocket.close(code=4401)
                return
            async for event in tasks.events(since):
                await websocket.send_text(
                    json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
                )
        except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
            return

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/pair", pair, methods=["POST"]),
        Route("/status", status, methods=["GET"]),
        Route("/sessions/{session_id}/messages", message, methods=["POST"]),
        Route("/tasks/{task_id}/stop", stop, methods=["POST"]),
        WebSocketRoute("/sessions/{session_id}/events", events),
    ]
    return Starlette(routes=routes), pairing


def _authorized(header: str | None, pairing: PairingStore) -> bool:
    prefix = "Bearer "
    credential = header[len(prefix) :].strip() if header and header.startswith(prefix) else None
    return pairing.authenticate(credential)
