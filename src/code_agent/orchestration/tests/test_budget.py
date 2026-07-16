from __future__ import annotations

import unittest

from code_agent.orchestration.budget import (
    BudgetExceededError,
    BudgetLedger,
    ParentBudget,
)
from code_agent.orchestration.models import (
    AgentDefinition,
    AgentMode,
    AgentRole,
    AgentUsage,
    ChildRunRequest,
)
from code_agent.orchestration.modes import ModeRegistry, standard_mode_definitions
from code_agent.providers.config import ApiProtocol, ModelProfile, ProviderConfig


def _agent() -> AgentDefinition:
    profile = ModelProfile(
        "medium",
        ProviderConfig(
            "https://example.test",
            "model-medium",
            ApiProtocol.RESPONSES,
            api_key_env="TEST_KEY",
        ),
        200_000,
        8_192,
    )
    profiles = {mode.value: profile if mode is AgentMode.MEDIUM else ModelProfile(
        mode.value, profile.provider, 200_000, 8_192
    ) for mode in AgentMode}
    snapshot = ModeRegistry(
        standard_mode_definitions({mode: mode.value for mode in AgentMode})
    ).freeze("medium", profiles)
    return AgentDefinition(
        "worker", AgentRole.SUBAGENT, snapshot, "Perform bounded work."
    )


def _request(run_id: str, *, depth: int = 1, tokens: int = 40, tools: int = 2, seconds: int = 10) -> ChildRunRequest:
    return ChildRunRequest(
        "parent",
        "Inspect the requested area.",
        _agent(),
        depth,
        tokens,
        tools,
        seconds,
        run_id,
    )


class BudgetLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_leases_reserve_then_settle_actual_usage(self) -> None:
        ledger = BudgetLedger(ParentBudget(4, 2, 2, 100, 6, 30))
        first = await ledger.reserve(_request("first"))
        second = await ledger.reserve(_request("second"))

        reserved = await ledger.state()
        self.assertEqual(reserved.running_leases, 2)
        self.assertEqual(reserved.reserved, AgentUsage(80, 4, 20))

        await ledger.settle(first, AgentUsage(25, 1, 4))
        await ledger.release(second)
        final = await ledger.state()
        self.assertEqual(final.children_started, 2)
        self.assertEqual(final.running_leases, 0)
        self.assertEqual(final.used, AgentUsage(25, 1, 4))

    async def test_reservations_cannot_overcommit_parent_budget(self) -> None:
        ledger = BudgetLedger(ParentBudget(3, 2, 2, 60, 4, 20))
        await ledger.reserve(_request("first", tokens=40, tools=3, seconds=10))

        with self.assertRaisesRegex(BudgetExceededError, "token"):
            await ledger.reserve(_request("tokens", tokens=30, tools=1, seconds=5))
        with self.assertRaisesRegex(BudgetExceededError, "tool"):
            await ledger.reserve(_request("tools", tokens=10, tools=2, seconds=5))
        with self.assertRaisesRegex(BudgetExceededError, "depth"):
            await ledger.reserve(_request("deep", depth=3, tokens=10, tools=1, seconds=5))

    async def test_child_count_is_cumulative(self) -> None:
        ledger = BudgetLedger(ParentBudget(1, 1, 1, 100, 10, 20))
        lease = await ledger.reserve(_request("first"))
        await ledger.release(lease)

        with self.assertRaisesRegex(BudgetExceededError, "child count"):
            await ledger.reserve(_request("second"))

    async def test_overrun_is_charged_conservatively_and_rejected(self) -> None:
        ledger = BudgetLedger(ParentBudget(2, 1, 1, 100, 10, 20))
        lease = await ledger.reserve(_request("first", tokens=40, tools=2, seconds=10))

        with self.assertRaisesRegex(BudgetExceededError, "reserved"):
            await ledger.settle(lease, AgentUsage(45, 3, 12))
        state = await ledger.state()
        self.assertEqual(state.used, AgentUsage(40, 2, 10))
        self.assertEqual(state.running_leases, 0)


if __name__ == "__main__":
    unittest.main()
