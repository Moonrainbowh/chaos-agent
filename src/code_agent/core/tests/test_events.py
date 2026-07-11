from __future__ import annotations

import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.events import AgentEvent, EventKind  # noqa: E402


class AgentEventRoundTripTests(unittest.TestCase):
    def test_event_uses_utc_timestamp_and_round_trips(self) -> None:
        event = AgentEvent(
            kind=EventKind.ACTION_COMPLETED,
            payload={"request_id": "action-1", "result": {"ok": True}},
        )

        self.assertIsNotNone(event.timestamp.tzinfo)
        self.assertEqual(event.timestamp.utcoffset(), timedelta(0))
        encoded = event.to_dict()
        json.dumps(encoded)
        self.assertTrue(encoded["timestamp"].endswith("Z"))
        self.assertEqual(AgentEvent.from_dict(encoded), event)


class AgentEventValidationTests(unittest.TestCase):
    def test_event_is_frozen_and_does_not_share_payload_state(self) -> None:
        source = {"items": [{"value": 1}]}
        event = AgentEvent(kind=EventKind.MODEL_EVENT, payload=source)
        source["items"][0]["value"] = 2

        self.assertEqual(event.to_dict()["payload"], {"items": [{"value": 1}]})
        with self.assertRaises(TypeError):
            event.payload["extra"] = True  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            event.kind = EventKind.ERROR  # type: ignore[misc]

    def test_event_rejects_invalid_kind_payload_and_naive_timestamp(self) -> None:
        invalid_events = (
            lambda: AgentEvent(kind="error"),  # type: ignore[arg-type]
            lambda: AgentEvent(kind=EventKind.ERROR, payload=[]),  # type: ignore[arg-type]
            lambda: AgentEvent(
                kind=EventKind.ERROR, payload={"unsupported": {"set"}}
            ),
            lambda: AgentEvent(
                kind=EventKind.ERROR, timestamp=datetime(2026, 7, 10)
            ),
        )
        for constructor in invalid_events:
            with self.subTest(constructor=constructor):
                with self.assertRaises((TypeError, ValueError)):
                    constructor()

    def test_event_normalizes_aware_timestamp_to_utc(self) -> None:
        local_time = datetime(
            2026, 7, 10, 16, 0, tzinfo=timezone(timedelta(hours=8))
        )
        event = AgentEvent(kind=EventKind.RUN_STARTED, timestamp=local_time)

        self.assertEqual(event.timestamp.utcoffset(), timedelta(0))
        self.assertEqual(event.timestamp.hour, 8)


if __name__ == "__main__":
    unittest.main()
