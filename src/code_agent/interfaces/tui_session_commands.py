from __future__ import annotations

from .terminal_display import DisplayKind


async def handle_session_command(
    app: object,
    action: str | None,
    instruction: str | None,
) -> bool:
    if action in {None, "history", "历史"}:
        return await _show_history(app)
    peers = getattr(app, "peers", None)
    if peers is None:
        app._append(DisplayKind.ERROR, "peer controls are unavailable")
        return False
    arguments = _arguments(instruction)
    try:
        if action in {"online", "在线"}:
            return await _show_agents(app, peers)
        if action in {"rename", "重命名"}:
            session = await peers.rename(_unquote(arguments))
            app._append(
                DisplayKind.METADATA,
                f"renamed {_field(session, 'name')} · {_field(session, 'session_ref')}",
            )
        elif action in {"send", "发送"}:
            target, text = _send_arguments(arguments)
            result = await peers.send_message(target, text)
            message = getattr(result, "message", result)
            app._append(DisplayKind.METADATA, "sent " + _message_summary(message))
        elif action in {"inbound", "接收"}:
            if arguments not in {"auto", "accept", "hold", "refuse"}:
                raise ValueError("inbound policy must be auto, accept, hold, or refuse")
            session = await peers.set_inbound_policy(arguments)
            app._append(
                DisplayKind.METADATA,
                "inbound " + _field(session, "inbound_policy"),
            )
        elif action in {"inbox", "待处理"}:
            messages = await peers.list_inbox()
            app._append(DisplayKind.METADATA, _inbox_summary(messages))
        elif action in {"accept", "接受", "refuse", "拒绝"}:
            message = await peers.resolve_held(
                arguments, accept=action in {"accept", "接受"}
            )
            app._append(DisplayKind.METADATA, _message_summary(message))
        else:
            raise ValueError("unknown session action")
    except (KeyError, RuntimeError, TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    return True


async def _show_history(app: object) -> bool:
    sessions = getattr(app, "sessions", None)
    if sessions is None:
        app._append(DisplayKind.ERROR, "session history is unavailable")
        return False
    try:
        records = await sessions.list_threads()
    except (RuntimeError, TypeError, ValueError) as error:
        app._append(DisplayKind.ERROR, str(error))
        return False
    app._append(
        DisplayKind.METADATA,
        " | ".join(_field(item, "id", item) for item in records),
    )
    return True


async def _show_agents(app: object, peers: object) -> bool:
    records = await peers.list_agents()
    value = " | ".join(
        ":".join(
            (
                _field(item, "session_ref"),
                _field(item, "name"),
                _field(item, "status"),
                _field(item, "inbound_policy"),
            )
        )
        for item in records
    )
    app._append(DisplayKind.METADATA, value or "no online agents")
    return True


def _arguments(instruction: str | None) -> str:
    if not instruction:
        return ""
    parts = instruction.split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _send_arguments(arguments: str) -> tuple[str, str]:
    value = arguments.strip()
    if value[:1] in {'"', "'"}:
        quote = value[0]
        boundary = value.find(quote, 1)
        if boundary < 0:
            raise ValueError("send target has an unclosed quote")
        target, text = value[1:boundary], value[boundary + 1 :].lstrip()
    else:
        parts = value.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError("send requires a target and message text")
        target, text = parts
    if not target.strip() or not text.strip():
        raise ValueError("send requires a target and message text")
    return target, text


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _field(value: object, name: str, fallback: object = "unknown") -> str:
    selected = getattr(value, name, fallback)
    selected = getattr(selected, "value", selected)
    return _bounded(selected)


def _message_summary(message: object) -> str:
    return f"{_field(message, 'id')}:{_field(message, 'status')}"


def _inbox_summary(messages: object) -> str:
    value = " | ".join(
        f"{_message_summary(item)}:{_field(item, 'content', '')}"
        for item in messages
    )
    return value or "no pending peer messages"


def _bounded(value: object, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
