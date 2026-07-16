from __future__ import annotations

import copy
import unittest

from code_agent.plugins.manifest import (
    ManifestError,
    PluginTrustStore,
    manifest_digest,
    parse_manifest,
)


def manifest_data() -> dict[str, object]:
    raw: dict[str, object] = {
        "id": "review-helper",
        "namespace": "reviewer",
        "version": "1.0.0",
        "host_api": "1",
        "enabled": True,
        "contributions": {
            "tools": [
                {
                    "id": "inspect",
                    "description": "Inspect a typed host resource.",
                    "target": "read_file",
                    "risk": "read",
                    "input_schema": {"type": "object"},
                }
            ],
            "commands": [],
            "modes": [],
            "agents": [],
            "events": [
                {
                    "id": "done-note",
                    "event_kinds": ["task_completed"],
                    "ui": {
                        "primitive": "notify",
                        "title": "Review helper",
                        "prompt": "Review is available.",
                        "options": [],
                    },
                    "action": None,
                }
            ],
        },
    }
    raw["digest"] = manifest_digest(raw)
    return raw


class PluginManifestTests(unittest.TestCase):
    def test_untrusted_plugin_requires_explicit_activation(self) -> None:
        raw = manifest_data()

        inactive = parse_manifest(raw, "plugin.json", PluginTrustStore(), host_api="1")
        active = parse_manifest(raw, "plugin.json", PluginTrustStore(), host_api="1", approved=True)

        self.assertFalse(inactive.trusted)
        self.assertFalse(inactive.enabled)
        self.assertTrue(active.enabled)
        self.assertEqual(active.contributions.tools[0].target, "read_file")

    def test_matching_trust_digest_activates_without_separate_approval(self) -> None:
        raw = manifest_data()
        store = PluginTrustStore({"review-helper": raw["digest"]})

        plugin = parse_manifest(raw, "plugin.json", store, host_api="1")

        self.assertTrue(plugin.trusted)
        self.assertTrue(plugin.enabled)

    def test_digest_host_version_and_unknown_dynamic_fields_are_rejected(self) -> None:
        raw = manifest_data()
        changed = copy.deepcopy(raw)
        changed["namespace"] = "changed"
        with self.assertRaises(ManifestError):
            parse_manifest(changed, "plugin.json", PluginTrustStore(), host_api="1")
        with self.assertRaises(ManifestError):
            parse_manifest(raw, "plugin.json", PluginTrustStore(), host_api="2")

        callback = copy.deepcopy(raw)
        callback["python_callback"] = "module.run"
        callback["digest"] = manifest_digest(callback)
        with self.assertRaises(ManifestError):
            parse_manifest(callback, "plugin.json", PluginTrustStore(), host_api="1")


if __name__ == "__main__":
    unittest.main()
