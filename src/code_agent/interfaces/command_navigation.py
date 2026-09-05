from __future__ import annotations

from dataclasses import replace

from .command_availability import available_services
from .command_registry import REGISTRY
from .extension_picker import dynamic_picker_items, picker_context
from .picker import command_picker_items
from .runtime_picker import selection_blocked_reason


_SELECTORS = {"model", "effort"}


def command_rows(interactions: object, app: object) -> tuple[str, ...]:
    text, picker = app.input.text, interactions.picker
    if not text.startswith(("/", ":")):
        draft = getattr(app, "attachment_draft", None)
        return draft.rows() if draft is not None and draft.items else ()
    registry = getattr(app, "command_registry", REGISTRY)
    parent, query = picker_context(text, registry)
    dynamic = dynamic_picker_items(app)
    resource_prefix = getattr(interactions, "resource_prefix", "")
    if resource_prefix and text.startswith(resource_prefix):
        dynamic = (interactions.resource_items, text[len(resource_prefix):])
    spec, arguments = _tokens(app)
    picker.title = "COMMANDS" if spec is None else text[0] + spec.name
    picker.hint = _usage_hint(spec, arguments, registry)
    if dynamic is not None:
        items, query = dynamic
    elif spec is not None and arguments is not None and (
        not spec.actions or _has_argument_field(spec, arguments, registry)
    ):
        items, query = (), arguments
        picker.hint = "Usage: " + text[0] + spec.name + " " + spec.usage
        if spec.actions:
            action = registry.resolve_action(spec, arguments.split()[0])
            picker.hint = f"Usage: {text[0]}{spec.name} {action.name} {action.usage}"
    else:
        items = command_picker_items(
            registry.all(), available_services(app), parent=parent,
            command_prefix=text[0], include_advanced=bool(query.strip()),
        )
        items = _current_values(app, parent, items)
    picker.set_items(items)
    picker.update_query(query[:512])
    return picker.panel_rows(app._columns())


def _current_values(app: object, parent: object, items: tuple) -> tuple:
    if parent is None or parent.name not in {"mode", "permission"}:
        return items
    control = getattr(app, "task_modes" if parent.name == "mode" else "permissions", None)
    current = getattr(getattr(control, "current", None), "name", None)
    reason = selection_blocked_reason(app) if parent.name == "mode" else None
    return tuple(replace(
        item, detail=("Current · " if item.identifier.endswith(":" + str(current)) else "") + item.detail,
        enabled=item.enabled and reason is None,
        disabled_reason=reason or item.disabled_reason,
    ) for item in items)


async def handle_command_key(interactions: object, app: object, key: str) -> bool:
    if not app.input.text.startswith(("/", ":")):
        return False
    command_rows(interactions, app)
    picker = interactions.picker
    if key in {"up", "down"}:
        picker.move(-1 if key == "up" else 1)
    elif key == "\x1b":
        resource_prefix = getattr(interactions, "resource_prefix", "")
        back = getattr(interactions, "resource_back", "/") if app.input.text == resource_prefix else _parent_input(app)
        app.input.replace(back)
    elif key == "\t":
        selected = picker.accept()
        if selected is not None:
            app.input.replace(selected.completion)
    elif key == "\r":
        await _enter(interactions, app)
    else:
        return False
    return True


async def _enter(interactions: object, app: object) -> None:
    spec, arguments = _tokens(app)
    if spec is not None and arguments is None and (spec.actions or spec.name in _SELECTORS):
        app.input.replace(app.input.text[0] + spec.name + " ")
        return
    dynamic = dynamic_picker_items(app)
    prefix = getattr(interactions, "resource_prefix", "")
    resource = bool(prefix and app.input.text.startswith(prefix))
    if dynamic is not None or resource:
        await _accept(interactions, app, execute=True)
        return
    if spec is not None and _can_submit(app, spec, arguments):
        await app.submit(app.input.submit())
        return
    if spec is not None and arguments is not None and _has_argument_field(spec, arguments, app.command_registry):
        interactions.picker.error = "Enter the required argument, then press Enter."
        return
    await _accept(interactions, app, execute=False)


async def _accept(interactions: object, app: object, *, execute: bool) -> None:
    selected = interactions.picker.accept()
    if selected is None:
        return
    app.input.replace(selected.completion)
    spec, arguments = _tokens(app)
    if execute or (spec is not None and _can_submit(app, spec, arguments)):
        await app.submit(app.input.submit())


def _can_submit(app: object, spec: object, arguments: str | None) -> bool:
    if not arguments or not arguments.strip():
        return not spec.actions and spec.name not in _SELECTORS and not spec.usage.startswith("<")
    if not spec.actions:
        return True
    parts = arguments.split(maxsplit=1)
    action = app.command_registry.resolve_action(spec, parts[0])
    if action is None:
        return spec.name == "attach"
    return len(parts) > 1 or not action.usage.startswith("<")


def _has_argument_field(spec: object, arguments: str, registry: object) -> bool:
    parts = arguments.split(maxsplit=1)
    if not parts:
        return False
    action = registry.resolve_action(spec, parts[0]) if spec.actions else None
    return action is not None and bool(action.usage)


def _tokens(app: object) -> tuple[object | None, str | None]:
    head, space, arguments = app.input.text[1:].partition(" ")
    return getattr(app, "command_registry", REGISTRY).resolve(head), arguments if space else None


def _usage_hint(spec: object, arguments: str | None, registry: object) -> str:
    if spec is None:
        return "Type to filter · Advanced commands appear when searched"
    if arguments and spec.actions:
        action = registry.resolve_action(spec, arguments.split()[0])
        if action is not None:
            return action.description + (" · " + action.usage if action.usage else "")
    return spec.description + (" · " + spec.usage if spec.usage else "")


def _parent_input(app: object) -> str:
    text = app.input.text
    stripped = text.rstrip()
    if " " not in stripped:
        return "" if stripped in {"/", ":"} else text[0]
    spec, arguments = _tokens(app)
    if text != stripped and spec is not None and spec.actions:
        return text[0] + spec.name + " "
    return stripped.rsplit(" ", 1)[0] + " "
