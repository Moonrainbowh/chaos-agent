import unittest
from decimal import Decimal
from types import SimpleNamespace

from code_agent.core.events import AgentEvent, EventKind
from code_agent.core.models import ModelEvent, ModelEventKind, Usage
from code_agent.interfaces.usage_summary import UsageAccumulator, summarize_usage, usage_footer
from code_agent.interfaces.terminal_state import TerminalState
from code_agent.interfaces.history import RestoredThread


def usage_event(usage):
    return AgentEvent(EventKind.MODEL_EVENT,{"event":ModelEvent(ModelEventKind.USAGE,usage=usage).to_dict()})


class UsageSummaryTests(unittest.TestCase):
    def test_aggregate_seed_matches_historic_events_plus_live_requests(self):
        historical = [AgentEvent(EventKind.MODEL_STARTED, {"model": "model-a"}),
            usage_event(Usage(10, 2, 3, 0, True)),
            usage_event(Usage(12, 3, 4, 0, True)),
            AgentEvent(EventKind.MODEL_STARTED, {"model": "model-a"})]
        live = [AgentEvent(EventKind.MODEL_STARTED, {"model": "model-b"}),
            usage_event(Usage(20, 4)), usage_event(Usage(25, 5))]
        accumulator = UsageAccumulator()
        accumulator.seed(summarize_usage(historical))
        self.assertEqual(accumulator.summary, summarize_usage(historical))
        for event in live:
            accumulator.observe(event)
        self.assertEqual(accumulator.summary, summarize_usage(historical + live))
        self.assertEqual(len(accumulator._closed), 0)

    def test_footer_geometry_stays_within_narrow_terminal(self):
        from code_agent.interfaces.terminal_tail import render_live_tail_frame
        from code_agent.interfaces.terminal_renderer import Theme, ColorMode
        for width in (20,40,100):
            for height in (4,5,12):
                for expanded in (False,True):
                    with self.subTest(width=width,height=height,expanded=expanded):
                        frame = render_live_tail_frame("","idle",width,terminal_height=height,
                            status_context="↑100 ↓20 R50 W? CH50% 消耗120 费用未知\nmodel · context",
                            theme=Theme.SLATE,color=ColorMode.NEVER,expanded=expanded)
                        self.assertLessEqual(frame.geometry.height,height)
                        self.assertTrue(all(size <= width for size in frame.geometry.row_widths))

    def test_incremental_usage_replaces_request_and_survives_restore(self):
        events = [AgentEvent(EventKind.MODEL_STARTED,{}), usage_event(Usage(100,5,50,20,True)),
                  usage_event(Usage(100,10,50,20,True)), AgentEvent(EventKind.MODEL_STARTED,{}),
                  usage_event(Usage(200,30,100,0,True))]
        summary = summarize_usage(events)
        self.assertEqual((summary.input_tokens,summary.output_tokens,summary.cache_read,summary.cache_write),(300,40,150,20))
        self.assertEqual(summary.hit_rate,50)
        state = TerminalState()
        state.restore(RestoredThread("thread",(),tuple(events),(),()))
        self.assertEqual(state.usage.summary,summary)
        self.assertIn("CH50.0%",usage_footer(summary))

    def test_missing_usage_and_cache_are_unknown_not_zero(self):
        events = [AgentEvent(EventKind.MODEL_STARTED,{}),usage_event(Usage(10,5)),AgentEvent(EventKind.MODEL_STARTED,{})]
        summary = summarize_usage(events)
        self.assertTrue(summary.incomplete)
        self.assertIn("R? W? CH?",usage_footer(summary))
        self.assertIsNone(summary.estimated_cost(SimpleNamespace(input_cost_per_million=2,output_cost_per_million=8)))

    def test_known_zero_cache_and_configured_price(self):
        summary = summarize_usage([usage_event(Usage(1_000_000,100_000,0,0,True))])
        self.assertIn("R0 W0 CH0.0%",usage_footer(summary))
        self.assertEqual(summary.estimated_cost(SimpleNamespace(input_cost_per_million=2,output_cost_per_million=8)),Decimal("2.8"))

    def test_mixed_models_cannot_use_one_profile_price(self):
        events = []
        for name in ("model-a","model-b"):
            events.extend((AgentEvent(EventKind.MODEL_STARTED,{"model":name}),usage_event(Usage(100,20,0,0,True))))
        summary = summarize_usage(events)
        profile = SimpleNamespace(provider=SimpleNamespace(model="model-b"),input_cost_per_million=2,output_cost_per_million=8)
        self.assertEqual(summary.total_tokens,240)
        self.assertIsNone(summary.estimated_cost(profile))

    def test_old_usage_roundtrip_has_no_new_required_keys(self):
        usage = Usage.from_dict({"input_tokens":1,"output_tokens":2,"cached_input_tokens":0})
        self.assertEqual(usage.to_dict(),{"input_tokens":1,"output_tokens":2,"cached_input_tokens":0})
