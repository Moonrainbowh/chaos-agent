"""Explicit operator memory commands; all scope and persistence belong to Host."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from code_agent.sessions.errors import SessionNotFound

from .terminal_display import DisplayKind, safe_text

_HELP = (
    "/memory save <正文> | list [offset] | show <id> | revise <id> <正文> | "
    "withdraw <id> | delete <id> | search <查询>\n"
    "可选条件：save --conditions {\"key\":\"value\"} -- <正文>；revise 同样支持。"
)


async def handle_memory_command(app: Any, instruction: str | None) -> bool:
    """Delegate only user commands, with the current thread and version checks."""
    control = getattr(app, "project_memory", None)
    if control is None:
        app._append(DisplayKind.ERROR, "项目记忆不可用。")
        return False
    parts = (instruction or "list").split(maxsplit=1)
    action = parts[0]
    argument = parts[1] if len(parts) == 2 else ""
    thread = {"thread_id": app.current_thread_id}
    try:
        if action == "save":
            content, conditions = _content(argument)
            record = await control.save(content, conditions=conditions, **thread)
            await _show(app, record, "已保存项目记忆（显式用户输入，非验证证据）")
        elif action == "revise":
            parts = argument.split(maxsplit=1)
            if len(parts) != 2:
                raise ValueError("missing content")
            identifier, body = parts
            content, conditions = _content(body)
            old = await control.show(identifier, **thread)
            record = await control.revise(identifier, content,
                expected_revision=old.revision, conditions=conditions, **thread)
            await _show(app, record, "已修订项目记忆")
        elif action in {"show", "withdraw", "delete"}:
            identifier = _identifier(argument)
            old = await control.show(identifier, **thread)
            if action == "show":
                await _show(app, old, "项目记忆（参考，非验证证据）")
            elif action == "withdraw":
                record = await control.withdraw(identifier, revision=old.revision, **thread)
                await _show(app, record, "已撤回，不再用于默认召回")
            else:
                await control.delete(identifier, **thread)
                app._append(DisplayKind.METADATA, "已删除项目记忆：" + safe_text(identifier))
        elif action == "list":
            offset = int(argument) if argument else 0
            if offset < 0 or offset > 100000:
                raise ValueError("invalid offset")
            records = await control.list(limit=20, offset=offset, **thread)
            _list(app, records, offset)
        elif action == "search":
            if not argument:
                raise ValueError("missing query")
            records = await control.search(argument, limit=20, **thread)
            _list(app, records, None)
        else:
            raise ValueError("unknown action")
    except PermissionError:
        app._append(DisplayKind.ERROR, "当前会话或条目不属于此项目，无法访问项目记忆。")
        return False
    except (SessionNotFound, KeyError, LookupError):
        app._append(DisplayKind.ERROR, "此项目中未找到该记忆。")
        return False
    except ValueError:
        app._append(DisplayKind.ERROR, "参数无效或版本冲突；请重新查看后重试。\n" + _HELP)
        return False
    except (RuntimeError, TypeError, AttributeError) as error:
        # No exception body: it can contain SQL, paths or untrusted memory text.
        app._append(DisplayKind.ERROR,
                    "项目记忆操作失败（" + type(error).__name__ + "）；请重新查看版本后重试。")
        return False
    return True


def _identifier(value: str) -> str:
    if not value or any(char.isspace() for char in value):
        raise ValueError("one identifier required")
    return value


def _content(value: str) -> tuple[str, Mapping[str, object] | None]:
    conditions = None
    if value.startswith("--conditions "):
        remainder = value[len("--conditions "):].lstrip()
        conditions, end = json.JSONDecoder().raw_decode(remainder)
        if not isinstance(conditions, dict):
            raise ValueError("conditions must be an object")
        tail = remainder[end:].lstrip()
        if not tail.startswith("-- "):
            raise ValueError("conditions require delimiter")
        value = tail[3:]
    if not value.strip():
        raise ValueError("content required")
    return value, conditions


async def _show(app: Any, record: Any, heading: str) -> None:
    lifecycle = getattr(record.lifecycle, "value", record.lifecycle)
    applicability = await app.project_memory.applicability(
        record.memory_id, thread_id=app.current_thread_id)
    value = {
        "id": record.memory_id, "revision": record.revision,
        "applicability": applicability,
        "lifecycle": lifecycle, "kind": record.kind, "origin": record.origin,
        "conditions": dict(record.conditions), "source_refs": dict(record.source_refs),
        "content": record.content[:12000],
    }
    text = safe_text(json.dumps(value, ensure_ascii=False, indent=2))
    if len(text) > 12000:
        text = text[:12000] + "\n[显示已截断，原记忆保持完整]"
    app._append(DisplayKind.METADATA, heading + "\n" + text)


def _list(app: Any, records: Any, offset: int | None) -> None:
    rows = tuple(records)[:20]
    if not rows:
        app._append(DisplayKind.METADATA, "未找到项目记忆。")
        return
    rendered = ["项目记忆（参考，非验证证据）"]
    for record in rows:
        lifecycle = getattr(record.lifecycle, "value", record.lifecycle)
        rendered.append(safe_text(f"{record.memory_id} r{record.revision} {lifecycle} "
                                 f"{record.content[:180]}"))
    rendered.append("来源与条件：/memory show <id>" )
    if len(rows) == 20 and offset is not None:
        rendered.append(f"下一页：/memory list {offset + 20}")
    app._append(DisplayKind.METADATA, "\n".join(rendered))
