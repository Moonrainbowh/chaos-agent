from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.config.loader import LocalConfigError, load_runtime_config  # noqa: E402
from code_agent.providers.config import InputModality  # noqa: E402


def configuration(modalities: str = "") -> str:
    field = f"\ninput_modalities = {modalities}" if modalities else ""
    return (
        "[default]\nprovider = \"local\"\n\n"
        "[providers.local]\napi = \"responses\"\n"
        "base_url = \"https://api.example.test\"\nmodel = \"model\"\n"
        "api_key_env = \"TEST_KEY\"\ncontext_window = 1000\n"
        f"max_output_tokens = 100{field}\n"
    )


class InputModalityConfigTests(unittest.TestCase):
    def load(self, content: str):  # type: ignore[no-untyped-def]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "config.toml"
        path.write_text(content, encoding="utf-8")
        return load_runtime_config(env={"CHAOS_CONFIG": str(path)})

    def test_missing_modalities_defaults_to_text_only(self) -> None:
        runtime = self.load(configuration())
        self.assertEqual(
            runtime.profiles[0].input_modalities,
            frozenset({InputModality.TEXT}),
        )

    def test_explicit_image_modality_is_preserved(self) -> None:
        runtime = self.load(configuration('["text", "image"]'))
        self.assertEqual(
            runtime.profiles[0].input_modalities,
            frozenset({InputModality.TEXT, InputModality.IMAGE}),
        )

    def test_duplicates_unknown_and_image_only_fail_closed(self) -> None:
        for raw in ('["text", "text"]', '["audio"]', '["image"]'):
            with self.subTest(raw=raw), self.assertRaises(LocalConfigError):
                self.load(configuration(raw))


if __name__ == "__main__":
    unittest.main()
