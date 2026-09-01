from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.cancellation import CancellationToken  # noqa: E402
from code_agent.core.models import ActionRequest  # noqa: E402
from code_agent.peers.service import PeerMessagingService  # noqa: E402
from code_agent.sessions.repository import SQLiteSessionRepository  # noqa: E402
from code_agent_win.peer_tools import (  # noqa: E402
    PeerToolAdapter,
    validate_peer_tool_arguments,
)


class PeerToolAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        database = Path(self.temporary.name) / "sessions.sqlite3"
        repository = SQLiteSessionRepository(database)
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)
        self.sender = PeerMessagingService(
            repository,
            owner_pid=1001,
            owner_create_time=101.0,
            workspace_root="F:\\sender",
            permission_mode="ask",
            name="sender",
            instance_id="sender-instance",
            session_ref="sender-ref",
            clock=lambda: now,
        )
        self.receiver = PeerMessagingService(
            repository,
            owner_pid=1002,
            owner_create_time=102.0,
            workspace_root="F:\\receiver",
            permission_mode="ask",
            name="receiver",
            instance_id="receiver-instance",
            session_ref="receiver-ref",
            clock=lambda: now,
        )
        self.adapter = PeerToolAdapter(self.sender)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def asyncSetUp(self) -> None:
        await self.sender.register()
        await self.receiver.register()

    def test_definitions_are_independent_strict_objects(self) -> None:
        definitions = {item.name: item.parameters for item in self.adapter.tools()}
        self.assertEqual(
            set(definitions), {"list_agents", "send_message", "rename_agent"}
        )
        for schema in definitions.values():
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"])
            self.assertIsInstance(schema["required"], tuple)

    def test_root_preflight_validator_uses_safe_rendered_boundary(self) -> None:
        self.assertIsNone(validate_peer_tool_arguments("list_agents", {}))
        self.assertIsNone(
            validate_peer_tool_arguments(
                "send_message",
                {"target": "t" * 80, "message": chr(0x1F600) * 1_023},
            )
        )
        self.assertIsNotNone(
            validate_peer_tool_arguments(
                "send_message",
                {"target": "receiver", "message": chr(0x1F600) * 1_024},
            )
        )

    async def test_dispatch_lists_sends_and_renames_without_user_messages(self) -> None:
        cancellation = CancellationToken()
        listed = await self.adapter.dispatch(
            ActionRequest("call-list", "list_agents", {}), cancellation
        )
        self.assertFalse(listed.is_error)
        self.assertEqual(
            listed.output["agents"],
            ({"name": "receiver", "ref": "receiver-ref", "status": "idle"},),
        )

        sent = await self.adapter.dispatch(
            ActionRequest(
                "call-send",
                "send_message",
                {"target": "receiver-ref", "message": "plain peer text"},
            ),
            cancellation,
        )
        self.assertFalse(sent.is_error)
        claim = (await self.receiver.claim_inbox())[0]
        self.assertEqual(claim.message.content, "plain peer text")

        renamed = await self.adapter.dispatch(
            ActionRequest("call-rename", "rename_agent", {"name": "new-name"}),
            cancellation,
        )
        self.assertEqual(renamed.output, {"name": "new-name", "ref": "sender-ref"})

    async def test_invalid_and_unknown_requests_fail_closed(self) -> None:
        invalid = await self.adapter.dispatch(
            ActionRequest("call-invalid", "send_message", {"target": "receiver"}),
            CancellationToken(),
        )
        unknown = await self.adapter.dispatch(
            ActionRequest("call-unknown", "unknown_peer", {}), CancellationToken()
        )
        self.assertTrue(invalid.is_error)
        self.assertTrue(unknown.is_error)

    async def test_direct_adapter_rejects_oversized_utf8_message(self) -> None:
        oversized = chr(0x1F600) * 1_024
        result = await self.adapter.dispatch(
            ActionRequest(
                "call-oversized",
                "send_message",
                {"target": "receiver-ref", "message": oversized},
            ),
            CancellationToken(),
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.output, {"error": "invalid_peer_tool_arguments"})


if __name__ == "__main__":
    unittest.main()
