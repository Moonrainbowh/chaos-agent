"""Bind Host extension definitions across an asynchronous approval boundary."""
from dataclasses import dataclass
import hashlib
import json

from code_agent.core.models import ActionRequest


@dataclass(frozen=True)
class ExtensionActionBinding:
    plugin_fingerprint: str | None = None
    mcp_fingerprint: str | None = None
    mcp_generation: int | None = None

    def check(self, mcp, plugins, request, translated):
        current = capture_extension_action(mcp, plugins, request, translated, validate=False)
        if current != self:
            raise RuntimeError('extension definition changed; request a new action')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _definition(definitions, name):
    return next((item for item in definitions if item.name == name), None)


def _validate(definition, arguments):
    if definition is None:
        return
    # jsonschema/referencing are already dependencies of the locked MCP SDK.
    # Local $defs are supported; retrieval can never access network or files.
    from jsonschema.validators import validator_for
    from referencing import Registry
    from referencing.exceptions import NoSuchResource

    def unavailable(uri):
        raise NoSuchResource(ref=uri)

    schema = definition.to_dict()['parameters']
    try:
        validator = validator_for(schema)
        validator.check_schema(schema)
        validator(schema, registry=Registry(retrieve=unavailable)).validate(arguments)
    except Exception:
        raise ValueError('invalid extension arguments or schema') from None


def capture_extension_action(mcp, plugins, request, translated, *, validate=True):
    """Read immutable schema/risk/generation facts before the existing policy."""
    plugin_fingerprint = mcp_fingerprint = generation = None
    arguments = request.to_dict()['arguments']
    if translated is not request:
        if plugins is None or plugins.targets().get(request.name) != translated.name:
            raise RuntimeError('plugin target is unavailable')
        definitions = plugins.definitions()
        definition = _definition(definitions, request.name)
        if validate:
            _validate(definition, arguments)
        plugin_fingerprint = _digest({
            'generation': getattr(plugins, 'generation', None),
            'target': translated.name, 'risk': plugins.risk_map().get(request.name),
            'definition': definition.to_dict() if definition is not None else None,
        })
    if translated.name.startswith('mcp.'):
        if mcp is None:
            raise RuntimeError('MCP integration is unavailable')
        snapshot = getattr(mcp, 'snapshot', None)
        if callable(snapshot):
            current = snapshot()
            generation, definitions = current.generation, current.tools
            definition = _definition(definitions, translated.name)
            if definition is None:
                raise RuntimeError('MCP tool is unavailable')
        else:
            # Preserve the old custom dispatcher boundary; production controllers
            # always supply a generation-bound snapshot with a visible definition.
            definitions = getattr(mcp, 'definitions', lambda: ())()
            definition = _definition(definitions, translated.name)
        if validate:
            _validate(definition, arguments)
        risks = getattr(mcp, 'risks', lambda: {})()
        mcp_fingerprint = _digest({
            'generation': generation, 'risk': risks.get(translated.name),
            'definition': definition.to_dict() if definition is not None else None,
        })
    return ExtensionActionBinding(plugin_fingerprint, mcp_fingerprint, generation)
