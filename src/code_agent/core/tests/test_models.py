from __future__ import annotations

import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.models import (  # noqa: E402
    ActionRequest,
    ActionResult,
    ContextBundle,
    Message,
    ModelEvent,
    ModelEventKind,
    ToolCall,
    ToolDefinition,
    Usage,
)


class ModelRoundTripTests(unittest.TestCase):
    def assert_round_trip(self, value: object, model_type: type) -> None:
        encoded = value.to_dict()  # type: ignore[attr-defined]
        json.dumps(encoded)
        self.assertEqual(model_type.from_dict(encoded), value)

    def test_all_models_round_trip_through_json_compatible_dicts(self) -> None:
        tool_call = ToolCall(
            id="call-1",
            name="read_file",
            arguments={"path": "README.md", "lines": [1, 20]},
        )
        usage = Usage(input_tokens=12, output_tokens=5, cached_input_tokens=3)
        message = Message(
            role="assistant",
            content="",
            name="coder",
            tool_calls=(tool_call,),
        )
        request = ActionRequest(
            id="action-1",
            name="read_file",
            arguments={"path": "README.md"},
        )
        result = ActionResult(
            request_id="action-1",
            name="read_file",
            output={"text": "hello", "truncated": False},
            metadata={"duration_ms": 2.5},
        )
        definition = ToolDefinition(
            name="read_file",
            description="Read a text file.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
        bundle = ContextBundle(system_prompt="Be precise.", messages=(message,))

        for value in (
            tool_call,
            usage,
            message,
            request,
            result,
            definition,
            bundle,
            ModelEvent(kind=ModelEventKind.TEXT_DELTA, text="hello"),
            ModelEvent(kind=ModelEventKind.TOOL_CALL, tool_call=tool_call),
            ModelEvent(kind=ModelEventKind.USAGE, usage=usage),
            ModelEvent(kind=ModelEventKind.COMPLETED),
        ):
            with self.subTest(model=type(value).__name__, value=value):
                self.assert_round_trip(value, type(value))

        self.assertEqual(usage.total_tokens, 17)


class ModelValidationTests(unittest.TestCase):
    def test_message_rejects_unknown_role_and_invalid_name(self) -> None:
        for role in ("", "admin", 3):
            with self.subTest(role=role):
                with self.assertRaises((TypeError, ValueError)):
                    Message(role=role, content="hello")  # type: ignore[arg-type]

        for name in ("", "has spaces", "!unsafe"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    Message(role="assistant", content="hello", name=name)

    def test_named_models_reject_blank_ids_and_invalid_names(self) -> None:
        constructors = (
            lambda: ToolCall(id=" ", name="read_file", arguments={}),
            lambda: ActionRequest(id="", name="read_file", arguments={}),
            lambda: ActionResult(
                request_id="", name="read_file", output="finished"
            ),
            lambda: ToolDefinition(
                name="bad name", description="Read.", parameters={}
            ),
        )
        for constructor in constructors:
            with self.subTest(constructor=constructor):
                with self.assertRaises(ValueError):
                    constructor()

    def test_arguments_and_parameters_must_be_structured_json_mappings(self) -> None:
        invalid_arguments = (
            [],
            {1: "value"},
            {"unsupported": {1, 2}},
            {"not_finite": float("nan")},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises((TypeError, ValueError)):
                    ToolCall(
                        id="call-1",
                        name="read_file",
                        arguments=arguments,  # type: ignore[arg-type]
                    )

        with self.assertRaises(TypeError):
            ToolDefinition(
                name="read_file",
                description="Read.",
                parameters=[],  # type: ignore[arg-type]
            )

    def test_usage_rejects_negative_or_non_integer_counts(self) -> None:
        for value in (-1, 1.5, True):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    Usage(input_tokens=value)  # type: ignore[arg-type]

    def test_model_event_requires_payload_matching_its_kind(self) -> None:
        tool_call = ToolCall(id="call-1", name="read_file", arguments={})
        invalid_events = (
            lambda: ModelEvent(kind=ModelEventKind.TEXT_DELTA),
            lambda: ModelEvent(
                kind=ModelEventKind.TOOL_CALL, text="wrong", tool_call=tool_call
            ),
            lambda: ModelEvent(kind=ModelEventKind.USAGE),
            lambda: ModelEvent(kind=ModelEventKind.COMPLETED, text="wrong"),
        )
        for constructor in invalid_events:
            with self.subTest(constructor=constructor):
                with self.assertRaises(ValueError):
                    constructor()

    def test_model_event_rejects_payloads_with_wrong_types(self) -> None:
        invalid_events = (
            lambda: ModelEvent(
                kind=ModelEventKind.TOOL_CALL,
                tool_call="x",  # type: ignore[arg-type]
            ),
            lambda: ModelEvent(
                kind=ModelEventKind.USAGE,
                usage={},  # type: ignore[arg-type]
            ),
        )

        for constructor in invalid_events:
            with self.subTest(constructor=constructor):
                with self.assertRaises(TypeError):
                    constructor()


class ModelImmutabilityTests(unittest.TestCase):
    def test_models_copy_and_deeply_freeze_mutable_input(self) -> None:
        source = {"nested": {"items": [1, 2]}}
        call = ToolCall(id="call-1", name="read_file", arguments=source)
        source["nested"]["items"].append(3)  # type: ignore[index,union-attr]
        source["new"] = True

        self.assertEqual(
            call.to_dict()["arguments"], {"nested": {"items": [1, 2]}}
        )
        with self.assertRaises(TypeError):
            call.arguments["new"] = False  # type: ignore[index]
        nested = call.arguments["nested"]
        self.assertIsInstance(nested, dict.__mro__[1])
        with self.assertRaises((AttributeError, TypeError)):
            nested["items"].append(4)  # type: ignore[index,union-attr]

    def test_models_are_frozen_and_normalize_sequences_to_tuples(self) -> None:
        call = ToolCall(id="call-1", name="read_file", arguments={})
        calls = [call]
        message = Message(role="assistant", tool_calls=calls)  # type: ignore[arg-type]
        bundle = ContextBundle(system_prompt="Prompt", messages=[message])  # type: ignore[arg-type]
        calls.clear()

        self.assertEqual(message.tool_calls, (call,))
        self.assertEqual(bundle.messages, (message,))
        with self.assertRaises(FrozenInstanceError):
            message.content = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            ModelEvent(kind=ModelEventKind.COMPLETED).text = "changed"  # type: ignore[misc]

    def test_serialized_dict_does_not_share_state_with_model(self) -> None:
        request = ActionRequest(
            id="action-1",
            name="write_file",
            arguments={"content": {"lines": ["a"]}},
        )
        serialized = request.to_dict()
        serialized_arguments = serialized["arguments"]
        serialized_arguments["content"]["lines"].append("b")  # type: ignore[index,union-attr]

        self.assertEqual(
            request.to_dict()["arguments"], {"content": {"lines": ["a"]}}
        )


if __name__ == "__main__":
    unittest.main()
