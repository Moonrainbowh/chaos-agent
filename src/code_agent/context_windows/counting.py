"""Count the complete logical request; provider usage remains authoritative."""
import json


class PromptTokenCounter:
    def __init__(self, encoding=None):
        self.encoding = encoding
        self.label = "utf8-byte upper estimate" if encoding is None else "tokenizer estimate plus protocol reserve"

    def text(self, value):
        if self.encoding is None:
            return len(value.encode("utf-8"))
        return len(self.encoding.encode(value, disallowed_special=()))

    def request(self, system, messages, tools):
        if any(message.attachments for message in messages):
            raise ValueError("managed windows require an attachment-aware counter for multimodal input")
        # The model sees decoded text, not JSON transport escaping (e.g. backslash-n).
        total = self.text(system)
        for message in messages:
            total += self.text(message.content) + self.text(message.role)
            total += self.text(message.name or "") + self.text(message.tool_call_id or "")
            for call in message.tool_calls:
                total += self.text(json.dumps(call.to_dict(), ensure_ascii=False))
        for tool in tools:
            total += self.text(json.dumps(tool.to_dict(), ensure_ascii=False))
        return total + 256 + 16 * (len(messages) + len(tools))
