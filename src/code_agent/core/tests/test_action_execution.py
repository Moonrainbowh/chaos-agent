from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.action_execution import (  # noqa: E402
    ActionExecutionContext,
    ActionLineage,
)


class ActionExecutionTests(unittest.TestCase):
    def test_context_preserves_distinct_owner_origin_and_request_lineage(self) -> None:
        context = ActionExecutionContext(
            owner_thread_id="parent",
            origin_thread_id="child",
            request_id="request",
            task_id="task",
            parent_request_id="delegate",
        )

        self.assertEqual(context.owner_thread_id, "parent")
        self.assertEqual(context.origin_thread_id, "child")
        self.assertNotEqual(context.owner_thread_id, context.origin_thread_id)
        self.assertEqual(context.request_id, "request")
        self.assertEqual(context.task_id, "task")
        self.assertEqual(context.parent_request_id, "delegate")

    def test_required_identifiers_reject_invalid_values(self) -> None:
        cases = (
            (
                ActionLineage,
                {"owner_thread_id": "owner"},
                ("owner_thread_id",),
            ),
            (
                ActionExecutionContext,
                {
                    "owner_thread_id": "owner",
                    "origin_thread_id": "origin",
                    "request_id": "request",
                },
                ("owner_thread_id", "origin_thread_id", "request_id"),
            ),
        )
        invalid_values = (
            (3, TypeError),
            (" \t", ValueError),
            ("x" * 257, ValueError),
        )

        for model, valid_values, fields in cases:
            for field in fields:
                for invalid, error_type in invalid_values:
                    with self.subTest(model=model.__name__, field=field, value=invalid):
                        values = dict(valid_values)
                        values[field] = invalid
                        with self.assertRaises(error_type):
                            model(**values)  # type: ignore[arg-type]

    def test_optional_identifiers_reject_invalid_values_and_allow_none(self) -> None:
        cases = (
            (ActionLineage, {"owner_thread_id": "owner"}),
            (
                ActionExecutionContext,
                {
                    "owner_thread_id": "owner",
                    "origin_thread_id": "origin",
                    "request_id": "request",
                },
            ),
        )
        invalid_values = (
            (3, TypeError),
            (" \t", ValueError),
            ("x" * 257, ValueError),
        )

        for model, required_values in cases:
            instance = model(
                **required_values,
                task_id=None,
                parent_request_id=None,
            )
            self.assertIsNone(instance.task_id)
            self.assertIsNone(instance.parent_request_id)
            for field in ("task_id", "parent_request_id"):
                for invalid, error_type in invalid_values:
                    with self.subTest(model=model.__name__, field=field, value=invalid):
                        values = dict(required_values)
                        values[field] = invalid
                        with self.assertRaises(error_type):
                            model(**values)  # type: ignore[arg-type]

    def test_models_are_frozen(self) -> None:
        lineage = ActionLineage("owner", "task", "parent")
        context = ActionExecutionContext(
            "owner",
            "origin",
            "request",
            "task",
            "parent",
        )

        with self.assertRaises(FrozenInstanceError):
            lineage.owner_thread_id = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            context.origin_thread_id = "changed"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
