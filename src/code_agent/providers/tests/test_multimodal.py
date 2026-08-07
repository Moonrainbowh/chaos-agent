from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


SRC_ROOT = Path(__file__).resolve().parents[3]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from code_agent.core.attachments import AttachmentRef  # noqa: E402
from code_agent.core.models import Message  # noqa: E402
from code_agent.providers.anthropic import AnthropicClient  # noqa: E402
from code_agent.providers.config import (  # noqa: E402
    ApiProtocol,
    InputModality,
    ProviderConfig,
)
from code_agent.providers.errors import ProviderConfigError  # noqa: E402
from code_agent.providers.openai_chat import OpenAIChatClient  # noqa: E402
from code_agent.providers.openai_responses import OpenAIResponsesClient  # noqa: E402


class MemoryResolver:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def read(self, reference: AttachmentRef) -> bytes:
        return self.values[reference.sha256]


def reference(data: bytes, media_type: str, name: str) -> AttachmentRef:
    dimensions = (1, 1) if media_type == "image/png" else (None, None)
    return AttachmentRef(
        hashlib.sha256(data).hexdigest(),
        media_type,
        len(data),
        name,
        *dimensions,
    )


def config(api: ApiProtocol) -> ProviderConfig:
    return ProviderConfig(
        "https://api.example.test", "vision-model", api, "TEST_KEY", max_retries=0
    )


class ProviderMultimodalTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_protocols_emit_native_image_and_untrusted_text_blocks(self) -> None:
        image_data, text_data = b"normalized-png", b"console error"
        image = reference(image_data, "image/png", "screen.png")
        text = reference(text_data, "text/plain", "error.log")
        message = Message("user", "Diagnose", attachments=(image, text))
        resolver = MemoryResolver({image.sha256: image_data, text.sha256: text_data})
        seen: list[tuple[ApiProtocol, dict[str, object]]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.read())
            if request.url.path.endswith("/responses"):
                api = ApiProtocol.RESPONSES
                content = b'event: response.completed\ndata: {"type":"response.completed","response":{}}\n\n'
            elif request.url.path.endswith("/chat/completions"):
                api = ApiProtocol.CHAT_COMPLETIONS
                content = b"data: [DONE]\n\n"
            else:
                api = ApiProtocol.ANTHROPIC_MESSAGES
                content = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            seen.append((api, body))
            return httpx.Response(200, content=content)

        modalities = (InputModality.TEXT, InputModality.IMAGE)
        with patch.dict("os.environ", {"TEST_KEY": "key"}, clear=True):
            for api, client_type in (
                (ApiProtocol.RESPONSES, OpenAIResponsesClient),
                (ApiProtocol.CHAT_COMPLETIONS, OpenAIChatClient),
                (ApiProtocol.ANTHROPIC_MESSAGES, AnthropicClient),
            ):
                http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
                client = client_type(
                    config(api),
                    attachment_resolver=resolver,
                    input_modalities=modalities,
                    http_client=http_client,
                )
                _ = [item async for item in client.stream("", (message,), ())]
                await client.aclose()
                await http_client.aclose()

        responses = seen[0][1]["input"][0]["content"]  # type: ignore[index]
        chat = seen[1][1]["messages"][0]["content"]  # type: ignore[index]
        anthropic = seen[2][1]["messages"][0]["content"]  # type: ignore[index]
        self.assertEqual(responses[1]["type"], "input_image")  # type: ignore[index]
        self.assertEqual(chat[1]["type"], "image_url")  # type: ignore[index]
        self.assertEqual(anthropic[1]["type"], "image")  # type: ignore[index]
        for blocks in (responses, chat, anthropic):
            self.assertIn("Untrusted user attachment", str(blocks))
            self.assertIn(text.sha256[:12], str(blocks))

    async def test_text_only_profile_rejects_image_before_network(self) -> None:
        data = b"normalized-png"
        image = reference(data, "image/png", "screen.png")
        message = Message("user", attachments=(image,))
        requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(500)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenAIResponsesClient(
            config(ApiProtocol.RESPONSES),
            attachment_resolver=MemoryResolver({image.sha256: data}),
            http_client=http_client,
        )
        with patch.dict("os.environ", {"TEST_KEY": "key"}, clear=True):
            with self.assertRaises(ProviderConfigError):
                _ = [item async for item in client.stream("", (message,), ())]
        self.assertEqual(requests, 0)
        await client.aclose()
        await http_client.aclose()

    async def test_corrupt_resolver_fails_before_network(self) -> None:
        data = b"normalized-png"
        image = reference(data, "image/png", "screen.png")
        message = Message("user", attachments=(image,))
        requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(500)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenAIChatClient(
            config(ApiProtocol.CHAT_COMPLETIONS),
            attachment_resolver=MemoryResolver({image.sha256: b"corrupt"}),
            input_modalities=(InputModality.TEXT, InputModality.IMAGE),
            http_client=http_client,
        )
        with patch.dict("os.environ", {"TEST_KEY": "key"}, clear=True):
            with self.assertRaises(ProviderConfigError):
                _ = [item async for item in client.stream("", (message,), ())]
        self.assertEqual(requests, 0)
        await client.aclose()
        await http_client.aclose()


if __name__ == "__main__":
    unittest.main()
