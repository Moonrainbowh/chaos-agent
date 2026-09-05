import unittest

from code_agent.config.context_policy import configured_context_policy


class MetadataTests(unittest.TestCase):
    def test_trusted_metadata_defaults_enable_persistent(self):
        result = configured_context_policy({"model_metadata": {"token_budget": {
            "enabled": True, "work_tokens": 128000}}})
        self.assertEqual(result.strategy, "persistent")
        self.assertEqual(result.work_tokens, 128000)

    def test_explicit_config_and_disable_win(self):
        values = {"model_metadata": {"token_budget": {"enabled": True}}, "context_policy": False}
        self.assertIsNone(configured_context_policy(values))
        values["context_policy"] = {"strategy": "summary"}
        self.assertEqual(configured_context_policy(values).strategy, "summary")

    def test_no_name_inference_or_string_activation(self):
        self.assertIsNone(configured_context_policy({"model": "gpt-6-astra"}))
        self.assertIsNone(configured_context_policy({"model_metadata": {"token_budget": {}}}))
        with self.assertRaises(ValueError):
            configured_context_policy({"model_metadata": {"token_budget": {"enabled": "true"}}})
