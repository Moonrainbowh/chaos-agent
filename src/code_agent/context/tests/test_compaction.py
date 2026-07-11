from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.context.compaction import DeterministicCompactor  # noqa: E402
from code_agent.context.models import ContextConfig  # noqa: E402
from code_agent.core.models import Message, ToolCall  # noqa: E402


class DeterministicCompactorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def compactor(self, budget: int, recent: int = 2) -> DeterministicCompactor:
        config = ContextConfig(
            self.root,
            self.root,
            "System",
            message_tokens=budget,
            recent_messages=recent,
        )
        return DeterministicCompactor(config)

    def test_messages_within_budget_are_returned_verbatim(self) -> None:
        messages = (
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        )

        result = self.compactor(100).compact(messages)

        self.assertEqual(result.messages, messages)
        self.assertIs(result.messages[0], messages[0])
        self.assertEqual(result.removed_count, 0)
        self.assertIsNone(result.summary)
        self.assertLessEqual(result.estimated_tokens, 100)

    def test_older_content_becomes_bounded_checkpoint_with_action_names(self) -> None:
        call = ToolCall(
            id="call-1", name="read_file", arguments={"path": "old.py"}
        )
        messages = (
            Message(role="user", content="inspect " + "x" * 300),
            Message(role="assistant", tool_calls=(call,)),
            Message(
                role="tool",
                name="read_file",
                tool_call_id="call-1",
                content="old result " + "y" * 100,
            ),
            Message(role="user", content="latest request"),
        )

        result = self.compactor(60, recent=1).compact(messages)

        self.assertEqual(result.messages[0].role, "developer")
        self.assertIn("checkpoint", result.messages[0].content.casefold())
        self.assertIn("read_file", result.messages[0].content)
        self.assertEqual(result.messages[-1], messages[-1])
        self.assertGreater(result.removed_count, 0)
        self.assertLessEqual(result.estimated_tokens, 60)

    def test_tail_boundary_keeps_assistant_tool_call_adjacent_to_its_result(self) -> None:
        call = ToolCall(id="call-2", name="search", arguments={"q": "needle"})
        messages = (
            Message(role="user", content="old " + "z" * 600),
            Message(role="assistant", content="calling", tool_calls=(call,)),
            Message(role="tool", tool_call_id="call-2", content="found"),
            Message(role="user", content="latest"),
        )

        result = self.compactor(100, recent=2).compact(messages)
        compacted = result.messages

        tool_index = next(i for i, item in enumerate(compacted) if item.role == "tool")
        self.assertEqual(compacted[tool_index - 1].role, "assistant")
        self.assertEqual(compacted[tool_index - 1].tool_calls[0].id, "call-2")
        self.assertEqual(compacted[tool_index].tool_call_id, "call-2")
        self.assertEqual(compacted[-1].content, "latest")

    def test_orphan_tool_message_is_not_left_after_compaction(self) -> None:
        messages = (
            Message(role="user", content="old " + "x" * 500),
            Message(role="tool", tool_call_id="missing", content="orphan"),
            Message(role="user", content="latest"),
        )

        result = self.compactor(40, recent=3).compact(messages)

        self.assertNotIn("tool", [message.role for message in result.messages])
        self.assertEqual(result.messages[-1].content, "latest")

    def test_oversized_latest_user_is_truncated_but_retained_without_mutation(self) -> None:
        messages = (
            Message(role="assistant", content="old " + "a" * 200),
            Message(role="user", content="latest-" + "b" * 500),
        )
        before = json.dumps(
            [message.to_dict() for message in messages], sort_keys=True
        )

        result = self.compactor(18, recent=1).compact(messages)

        users = [message for message in result.messages if message.role == "user"]
        self.assertEqual(len(users), 1)
        self.assertTrue(users[0].content.startswith("latest-"))
        self.assertLess(len(users[0].content), len(messages[-1].content))
        self.assertLessEqual(result.estimated_tokens, 18)
        self.assertEqual(
            json.dumps([message.to_dict() for message in messages], sort_keys=True),
            before,
        )

    def test_repeated_compaction_is_byte_stable(self) -> None:
        messages = tuple(
            Message(role="user" if index % 2 == 0 else "assistant", content=str(index) * 80)
            for index in range(8)
        )
        compactor = self.compactor(55, recent=2)

        first = compactor.compact(messages)
        second = compactor.compact(messages)

        encoded_first = json.dumps(
            [message.to_dict() for message in first.messages],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded_second = json.dumps(
            [message.to_dict() for message in second.messages],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(encoded_first, encoded_second)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
