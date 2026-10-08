from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from code_agent.sessions.errors import SessionError, SessionNotFound
from code_agent.project_launcher.store import ProjectStore, ProjectStoreError
from chaos_agent.app_paths import product_state_root

from .errors import DeviceAuthorizationError, RemoteConflict, RemoteInputError, RemoteNotFound
from .pairing import PairingStore
from .task_controller import RemoteTaskController
from .requests import RemoteRequestControl


def create_host_app(
    application: object, *, pairing: PairingStore | None = None,
    application_factory: Callable[[Path], Any] | None = None,
    project_store: ProjectStore | None = None,
) -> tuple[Starlette, PairingStore]:
    pairing = pairing or PairingStore()
    project_store = project_store or getattr(application, "project_store", None) or ProjectStore(product_state_root() / "projects.json")
    tasks = RemoteTaskController(application, application_factory=application_factory, project_store=project_store)
    requests = RemoteRequestControl(tasks)
    static_path = Path(__file__).with_name("static") / "index.html"

    @asynccontextmanager
    async def lifespan(app: Starlette):
        try:
            reconcile = getattr(application.foreground_tasks, "reconcile_stale_tasks", None)
            if callable(reconcile):
                await reconcile()
            yield
        finally:
            await tasks.aclose()

    async def index(request: object) -> HTMLResponse:
        return HTMLResponse(await asyncio.to_thread(static_path.read_text, encoding="utf-8"))

    async def pair(request: Any) -> JSONResponse:
        try:
            body = await _object_body(request)
        except RemoteInputError:
            return JSONResponse({"error": "expected a JSON object"}, status_code=400)
        try:
            credential = await asyncio.to_thread(pairing.pair, body.get("token", ""))
        except PermissionError:
            return JSONResponse({"error": "invalid pairing token"}, status_code=401)
        except OSError:
            return JSONResponse({"error": "cannot save pairing; retry"}, status_code=503)
        return JSONResponse({"device_credential": credential})

    def authenticated(handler):
        async def invoke(request: Any) -> JSONResponse:
            if not await asyncio.to_thread(_authorized, request.headers.get("authorization"), pairing):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                return JSONResponse(await handler(request))
            except RemoteInputError as error:
                return JSONResponse({"error": str(error)}, status_code=400)
            except ProjectStoreError as error:
                return JSONResponse({"error": str(error)}, status_code=400)
            except RemoteNotFound as error:
                return JSONResponse({"error": error.args[0]}, status_code=404)
            except SessionNotFound:
                return JSONResponse({"error": "unknown session"}, status_code=404)
            except SessionError:
                return JSONResponse({"error": "session storage is unavailable; retry"}, status_code=503)
            except RemoteConflict as error:
                return JSONResponse({"error": str(error)}, status_code=409)
            except DeviceAuthorizationError:
                return JSONResponse({"error": "device authorization is revoked or invalid"}, status_code=401)
            except PermissionError:
                return JSONResponse({"error": "operation permission denied"}, status_code=403)
            except ValueError:
                return JSONResponse({"error": "request is incompatible with the saved session"}, status_code=400)
            except RuntimeError:
                return JSONResponse({"error": "request cannot run right now"}, status_code=409)
            except Exception:
                return JSONResponse({"error": "Host request failed; retry"}, status_code=503)
        return invoke

    async def status(request: Any) -> dict[str, Any]:
        return {"host": "online", **await tasks.status()}

    async def projects(request: Any) -> dict[str, Any]:
        snapshot = await tasks.catalog.snapshot()
        ordered = sorted(snapshot.projects.values(), key=lambda row: (row["recent"], row["updated_at"] or ""), reverse=True)
        return {"projects": ordered, "current_project_id": snapshot.current_project_id}

    async def add_project(request: Any) -> dict[str, Any]:
        body = await _object_body(request)
        entry = await tasks.catalog.project_operation(project_store.add, _project_path(body.get("path")))
        return {"project": (await tasks.catalog.snapshot()).project(entry.identifier)}

    async def remove_project(request: Any) -> dict[str, Any]:
        identifier = _identifier(request.path_params["project_id"], "project_id")
        project = (await tasks.catalog.snapshot()).project(identifier)
        if not project["registered"]:
            raise RemoteNotFound("project entry is not registered")
        await tasks.catalog.project_operation(project_store.remove, Path(project["path"]))
        return {"removed_project_id": identifier}

    async def select_project(request: Any) -> dict[str, Any]:
        identifier = _identifier(request.path_params["project_id"], "project_id")
        project = (await tasks.catalog.snapshot()).project(identifier)
        # A historical project may have had its shortcut removed; explicit selection restores it.
        def select():
            project_store.add(Path(project["path"]))
            project_store.select(Path(project["path"]))
        await tasks.catalog.project_operation(select)
        return {"project": (await tasks.catalog.snapshot()).project(identifier)}

    async def directories(request: Any) -> dict[str, Any]:
        value = request.query_params.get("path")
        if value is None:
            roots = await asyncio.to_thread(project_store.browse_roots)
            return {"path": None, "parent": None, "directories": [_directory(root) for root in roots]}
        root = await asyncio.to_thread(_project_path(value).resolve, strict=False)
        children = await asyncio.to_thread(project_store.child_directories, root)
        return {"path": str(root), "parent": str(root.parent) if root.parent != root else None,
                "directories": [_directory(child) for child in children]}

    async def sessions(request: Any) -> dict[str, Any]:
        query = request.query_params.get("q", "").strip()
        if len(query) > 512:
            raise RemoteInputError("q must be at most 512 characters")
        selected = request.query_params.get("project_id") or None
        if selected is not None:
            _identifier(selected, "project_id")
        return await tasks.catalog.list_sessions(
            selected_project=selected, query=query,
            offset=_integer(request.query_params, "offset", default=0, minimum=0, maximum=2**63 - 1),
            limit=_integer(request.query_params, "limit", default=40, minimum=1, maximum=100),
        )

    async def create_session(request: Any) -> dict[str, Any]:
        body = await _object_body(request)
        identifier = _identifier(body.get("project_id"), "project_id")
        return await tasks.catalog.create_session(identifier)

    async def history(request: Any) -> dict[str, Any]:
        identifier = _identifier(request.path_params["session_id"], "session_id")
        before = None
        if "before" in request.query_params:
            before = _integer(request.query_params, "before", default=0, minimum=0, maximum=2**63 - 1)
        return await tasks.history(
            identifier, before=before,
            limit=_integer(request.query_params, "limit", default=50, minimum=1, maximum=200),
        )

    async def message(request: Any) -> dict[str, Any]:
        identifier = _identifier(request.path_params["session_id"], "session_id")
        body = await _object_body(request)
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 1024:
            raise RemoteInputError("prompt must be 1..1024 characters")
        return await tasks.start(prompt, identifier)

    async def stop(request: Any) -> dict[str, str]:
        identifier = _identifier(request.path_params["task_id"], "task_id")
        await tasks.stop(identifier)
        return {"status": "stopped"}

    async def pending_requests(request: Any) -> dict[str, Any]:
        identifier = _identifier(request.path_params["session_id"], "session_id")
        return await requests.pending(identifier)

    async def respond_request(request: Any) -> dict[str, Any]:
        task_id = _identifier(request.path_params["task_id"], "task_id")
        request_id = _identifier(request.path_params["request_id"], "request_id")
        body = await _object_body(request)
        if "request_id" in body and body["request_id"] != request_id:
            raise RemoteInputError("request identity does not match the path")
        body["request_id"] = request_id
        header = request.headers.get("authorization", "")
        credential = header.removeprefix("Bearer ") if header.startswith("Bearer ") else None
        async def authenticate_response() -> bool:
            if not await asyncio.to_thread(pairing.authenticate, credential):
                raise DeviceAuthorizationError("device credential is revoked or invalid")
            return True
        # The same file lock serializes external CLI revocation with durable consumption.
        async with pairing.authorized_response(credential):
            try:
                return await requests.respond(task_id, body,
                    authenticate=authenticate_response)
            except ValueError as error:
                raise RemoteConflict("request expired, consumed or changed; refresh its durable snapshot") from error

    async def events(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            auth_message = await asyncio.wait_for(websocket.receive_text(), timeout=5)
            credential = auth_message.removeprefix("auth:") if auth_message.startswith("auth:") else None
            if not await asyncio.to_thread(pairing.authenticate, credential):
                await websocket.close(code=4401)
                return
            try:
                identifier = _identifier(websocket.path_params["session_id"], "session_id")
                since = _integer(websocket.query_params, "since", default=0, minimum=0, maximum=2**63 - 1)
                epoch = websocket.query_params.get("epoch")
                if epoch is not None:
                    _identifier(epoch, "epoch")
            except RemoteInputError:
                await websocket.close(code=4400)
                return
            if identifier != "current":
                try:
                    (await tasks.catalog.snapshot()).session(identifier)
                except (RemoteNotFound, SessionNotFound):
                    await websocket.close(code=4404)
                    return
            await _authenticated_events(websocket, tasks, pairing, credential, since, identifier, epoch)
        except asyncio.TimeoutError:
            await websocket.close(code=4401)
        except (WebSocketDisconnect, RuntimeError):
            return
        except Exception:
            with suppress(RuntimeError):
                await websocket.close(code=1011)

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/pair", pair, methods=["POST"]),
        Route("/status", authenticated(status), methods=["GET"]),
        Route("/projects", authenticated(projects), methods=["GET"]),
        Route("/projects", authenticated(add_project), methods=["POST"]),
        Route("/projects/{project_id}", authenticated(remove_project), methods=["DELETE"]),
        Route("/projects/{project_id}/select", authenticated(select_project), methods=["POST"]),
        Route("/project-directories", authenticated(directories), methods=["GET"]),
        Route("/sessions", authenticated(sessions), methods=["GET"]),
        Route("/sessions", authenticated(create_session), methods=["POST"]),
        Route("/sessions/{session_id}/messages", authenticated(history), methods=["GET"]),
        Route("/sessions/{session_id}/messages", authenticated(message), methods=["POST"]),
        Route("/tasks/{task_id}/stop", authenticated(stop), methods=["POST"]),
        Route("/sessions/{session_id}/requests", authenticated(pending_requests), methods=["GET"]),
        Route("/tasks/{task_id}/requests/{request_id}/respond", authenticated(respond_request), methods=["POST"]),
        WebSocketRoute("/sessions/{session_id}/events", events),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.remote_tasks = tasks
    return app, pairing


def _project_path(value: object) -> Path:
    if not isinstance(value, str) or not value or len(value) > 32767 or "\0" in value:
        raise RemoteInputError("path must be an absolute directory path")
    root = Path(value)
    if not root.is_absolute():
        raise RemoteInputError("path must be an absolute directory path")
    return root


def _directory(root: Path) -> dict[str, str]:
    return {"name": root.name or str(root), "path": str(root)}


async def _object_body(request: Any) -> dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise RemoteInputError("expected a JSON object") from None
    if not isinstance(body, dict):
        raise RemoteInputError("expected a JSON object")
    return body


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise RemoteInputError(f"{field} must be a valid identifier")
    return value


def _integer(parameters: Any, field: str, *, default: int, minimum: int, maximum: int) -> int:
    value = parameters.get(field, str(default))
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value):
        raise RemoteInputError(f"{field} must be an integer")
    number = int(value)
    if number < minimum or number > maximum:
        raise RemoteInputError(f"{field} must be between {minimum} and {maximum}")
    return number


async def _authenticated_events(
    websocket: WebSocket, tasks: RemoteTaskController, pairing: PairingStore,
    credential: str, since: int, session_id: str = "current", host_epoch: str | None = None,
) -> None:
    async def stream():
        events = tasks.events(since, session_id) if host_epoch is None else tasks.events(since, session_id, host_epoch=host_epoch)
        async for event in events:
            if not await asyncio.to_thread(pairing.authenticate, credential):
                await websocket.close(code=4401)
                return
            await websocket.send_text(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")))
        await websocket.close(code=1000)

    async def watch_revocation():
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=0.25)
                if message["type"] == "websocket.disconnect":
                    return
            except asyncio.TimeoutError:
                pass
            if not await asyncio.to_thread(pairing.authenticate, credential):
                await websocket.close(code=4401)
                return

    workers = [asyncio.create_task(stream()), asyncio.create_task(watch_revocation())]
    try:
        done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
        for worker in done:
            worker.result()
    finally:
        for worker in workers:
            worker.cancel()
        for worker in workers:
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await worker


def _authorized(header: str | None, pairing: PairingStore) -> bool:
    prefix = "Bearer "
    credential = header[len(prefix):].strip() if header and header.startswith(prefix) else None
    return pairing.authenticate(credential)
