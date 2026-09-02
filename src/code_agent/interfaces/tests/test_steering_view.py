from __future__ import annotations

import unittest

from code_agent.core.events import AgentEvent, EventKind
from code_agent.interfaces.steering_view import (
    SteeringKind,
    SteeringQueueView,
    SteeringStage,
)
from code_agent.interfaces.tui_interactions import TuiInteractions


class SteeringQueueViewTests(unittest.TestCase):
    def test_stages_are_visible_ordered_and_never_move_backward(self) -> None:
        queue = SteeringQueueView()
        item = queue.queue("inspect tests first", "control-1")

        self.assertEqual(queue.status_line(), "[排队] queued · queue 1")
        queue.transition(item.identifier, SteeringStage.STEERED)
        queue.transition(item.identifier, SteeringStage.DEQUEUED)
        queue.transition(item.identifier, SteeringStage.APPLIED)
        self.assertEqual(queue.status_line(), "[排队] applied · queue 0")
        with self.assertRaises(ValueError):
            queue.transition(item.identifier, SteeringStage.QUEUED)

    def test_persisted_turn_and_context_events_drive_dequeued_and_applied(self) -> None:
        class App:
            def __init__(self):
                self.lines = []

            def _append(self, kind, text):
                self.lines.append(text)

        app = App()
        interactions = TuiInteractions()
        item = interactions.steering.queue(
            "new direction", "control-1", kind=SteeringKind.STEER
        )
        interactions.steering.transition(item.identifier, SteeringStage.STEERED)

        interactions.observe_event(app, AgentEvent(EventKind.TURN_STARTED))
        self.assertEqual(interactions.steering.items[0].stage, SteeringStage.DEQUEUED)
        interactions.observe_event(app, AgentEvent(EventKind.CONTEXT_BUILT))
        self.assertEqual(interactions.steering.items[0].stage, SteeringStage.APPLIED)
        self.assertEqual(
            app.lines,
            ["[转向] dequeued · queue 1", "[转向] applied · queue 0"],
        )

    def test_followup_is_applied_only_by_its_promotion_event(self) -> None:
        class App:
            def __init__(self):
                self.lines = []

            def _append(self, kind, text):
                self.lines.append(text)

        app = App()
        interactions = TuiInteractions()
        interactions.steering.queue("later", "followup-1")

        interactions.observe_event(app, AgentEvent(EventKind.TURN_STARTED))
        self.assertEqual(interactions.steering.pending_count, 1)
        interactions.observe_event(
            app,
            AgentEvent(
                EventKind.TASK_FOLLOWUPS_PROMOTED,
                {"ids": ["followup-1"], "count": 1},
            ),
        )

        self.assertEqual(interactions.steering.pending_count, 0)
        self.assertEqual(app.lines, ["[排队] applied · queue 0"])

    def test_promotion_racing_ahead_of_view_creation_is_retained(self) -> None:
        queue = SteeringQueueView()

        self.assertFalse(queue.mark_applied("followup-1"))
        item = queue.queue("later", "followup-1")

        self.assertEqual(item.stage, SteeringStage.APPLIED)
        self.assertEqual(queue.pending_count, 0)


if __name__ == "__main__":
    unittest.main()
