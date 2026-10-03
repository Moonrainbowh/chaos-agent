import unittest
from code_agent.providers.anthropic import _UsageState
from code_agent.providers.pi_messages import _usage
from code_agent.providers.errors import ProviderProtocolError


class CacheUsageTests(unittest.TestCase):
    def test_anthropic_cache_write_is_in_total_and_survives_output_snapshot(self):
        state = _UsageState()
        state.update({"input_tokens":10,"cache_read_input_tokens":40,"cache_creation_input_tokens":20})
        usage = state.update({"output_tokens":7}).usage
        self.assertEqual((usage.input_tokens,usage.output_tokens,usage.cached_input_tokens,usage.cache_write_input_tokens),(70,7,40,20))
        self.assertTrue(usage.cache_read_known)

    def test_pi_caches_are_counted_once_and_malformed_counts_fail(self):
        usage = _usage({"input":10,"output":7,"cacheRead":40,"cacheWrite":20}).usage
        self.assertEqual(usage.total_tokens,77)
        for count in (True,-1,"20"):
            with self.subTest(count=count), self.assertRaises(ProviderProtocolError):
                _usage({"input":count,"cacheRead":30})
