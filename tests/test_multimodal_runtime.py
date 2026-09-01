from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from code_agent.attachments.ingest import AttachmentIngestor
from code_agent.attachments.store import AttachmentStore
from code_agent.core.attachments import AttachmentRef
from code_agent.core.models import Message
from code_agent.providers.config import (
    ApiProtocol,
    InputModality,
    ModelProfile,
    ProviderConfig,
)
from code_agent.providers.anthropic import AnthropicClient
from code_agent.providers.openai_responses import OpenAIResponsesClient
from code_agent_win.runtime_support import (
    model_client,
    profile_model_factory,
    replace_model,
)
from tests.agent_app_test_support import _configured_application


def profile() -> ModelProfile:
    provider = ProviderConfig(
        "https://api.example.test",
        "vision-model",
        ApiProtocol.RESPONSES,
        "TEST_KEY",
    )
    return ModelProfile(
        "vision",
        provider,
        8_000,
        1_000,
        input_modalities=frozenset(
            {InputModality.TEXT, InputModality.IMAGE}
        ),
    )


class MultimodalRuntimeTests(unittest.TestCase):
    def test_real_factory_binds_shared_store_and_profile_modalities(self) -> None:
        selected = profile()
        with tempfile.TemporaryDirectory() as directory:
            store = AttachmentStore(Path(directory) / "attachments")
            reference = store.put(
                b"normalized-png",
                media_type="image/png",
                display_name="screen.png",
                width=1,
                height=1,
            )
            factory = profile_model_factory(
                model_client, {selected.name: selected}, store
            )

            client = factory(selected.provider, reasoning_effort="max")
            content = client._attachments.responses(  # type: ignore[attr-defined]
                Message("user", attachments=(reference,))
            )

        self.assertIsInstance(client, OpenAIResponsesClient)
        self.assertEqual(client._request_options.reasoning_effort, "max")
        self.assertEqual(
            client._request_options.max_output_tokens,
            selected.max_output_tokens,
        )
        self.assertEqual(content[0]["type"], "input_image")  # type: ignore[index]

    def test_one_argument_fake_factory_remains_compatible(self) -> None:
        selected = profile()
        calls: list[object] = []

        def fake(provider: object) -> object:
            calls.append(provider)
            return object()

        factory = profile_model_factory(
            fake, {selected.name: selected}, None
        )
        result = factory(selected.provider, reasoning_effort="high")

        self.assertIsNotNone(result)
        self.assertEqual(calls, [selected.provider])

    def test_anthropic_factory_keeps_effort_off_the_wire(self) -> None:
        provider = ProviderConfig(
            "https://api.example.test",
            "claude-test",
            ApiProtocol.ANTHROPIC_MESSAGES,
            "TEST_KEY",
        )
        selected = ModelProfile("claude", provider, 8_000, 777)
        factory = profile_model_factory(
            model_client, {selected.name: selected}, None
        )

        client = factory(selected.provider, reasoning_effort="max")

        self.assertIsInstance(client, AnthropicClient)
        self.assertIsNone(client._request_options.reasoning_effort)
        self.assertEqual(client._request_options.max_output_tokens, 777)
        asyncio.run(client.aclose())

    def test_shared_provider_with_conflicting_modalities_is_rejected(self) -> None:
        shared = profile().provider
        text = ModelProfile("text", shared, 8_000, 1_000)
        vision = ModelProfile(
            "vision",
            shared,
            8_000,
            1_000,
            input_modalities=frozenset(
                {InputModality.TEXT, InputModality.IMAGE}
            ),
        )

        with self.assertRaisesRegex(ValueError, "conflicting input modalities"):
            profile_model_factory(
                model_client,
                {text.name: text, vision.name: vision},
                None,
            )

    def test_model_override_preserves_input_modalities(self) -> None:
        selected = profile()
        replaced = replace_model(selected, "other-model")
        self.assertEqual(replaced.input_modalities, selected.input_modalities)

    def test_application_exposes_one_shared_store_and_ingestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            application, _, product = _configured_application(Path(directory))

        self.assertIsInstance(application.attachment_store, AttachmentStore)
        self.assertIsInstance(application.attachment_ingestor, AttachmentIngestor)
        self.assertIs(
            application.attachment_ingestor.store,
            application.attachment_store,
        )
        self.assertEqual(
            application.attachment_store.root,
            (product / "attachments").resolve(),
        )
        self.assertIsNotNone(application.tui.attachment_draft)
        reference = AttachmentRef(
            "a" * 64,
            "image/png",
            8,
            "screen.png",
            1,
            1,
        )
        with self.assertRaisesRegex(RuntimeError, "does not support"):
            application.tui.attachment_draft.validate((reference,))


if __name__ == "__main__":
    unittest.main()
