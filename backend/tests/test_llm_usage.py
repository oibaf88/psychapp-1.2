import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.llm.anthropic_provider import AnthropicProvider


class AnthropicUsageTests(unittest.TestCase):
    def _response(self, text='{"ok": true}'):
        usage = SimpleNamespace(
            input_tokens=101,
            output_tokens=79,
            output_tokens_details=SimpleNamespace(thinking_tokens=61),
            cache_creation_input_tokens=7000,
            cache_read_input_tokens=2000,
            cache_creation=SimpleNamespace(
                ephemeral_5m_input_tokens=7000,
                ephemeral_1h_input_tokens=0,
            ),
            server_tool_use=SimpleNamespace(web_search_requests=0, web_fetch_requests=0),
        )
        return SimpleNamespace(
            id="msg_usage_test",
            model="claude-opus-5",
            _request_id="req_usage_test",
            stop_reason="end_turn",
            usage=usage,
            content=[SimpleNamespace(type="text", text=text)],
        )

    def test_structured_call_uses_prompt_cache_and_preserves_usage_breakdown(self):
        create = MagicMock(return_value=self._response())
        provider = AnthropicProvider()
        provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))

        result = provider.analyze_structured(
            "large static clinical prompt",
            "short patient message",
            {"input_schema": {"type": "object", "properties": {}}},
        )

        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["cache_control"], {"type": "ephemeral"})
        self.assertEqual(result.metadata.input_tokens, 101)
        self.assertEqual(result.metadata.output_tokens, 79)
        self.assertEqual(result.metadata.thinking_tokens, 61)
        self.assertEqual(result.metadata.cache_creation_input_tokens, 7000)
        self.assertEqual(result.metadata.cache_read_input_tokens, 2000)
        self.assertEqual(result.metadata.cache_creation_5m_input_tokens, 7000)
        self.assertEqual(result.metadata.cache_creation_1h_input_tokens, 0)
        self.assertEqual(result.metadata.web_search_requests, 0)
        self.assertEqual(result.metadata.web_fetch_requests, 0)

    def test_chat_usage_recorder_receives_prompt_sizes_without_prompt_text(self):
        recorder = MagicMock()
        create = MagicMock(return_value=self._response("respuesta"))
        provider = AnthropicProvider(usage_recorder=recorder)
        provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))

        provider.chat(
            "system prompt",
            [
                {"role": "user", "content": "hola"},
                {"role": "assistant", "content": "hola, te leo"},
                {"role": "user", "content": "continúa"},
            ],
            max_tokens=2000,
        )

        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["cache_control"], {"type": "ephemeral"})
        recorded = recorder.call_args.kwargs
        self.assertEqual(recorded["call_kind"], "chat")
        self.assertEqual(recorded["status"], "succeeded")
        self.assertEqual(recorded["system_chars"], len("system prompt"))
        self.assertEqual(recorded["message_chars"], len("hola") + len("hola, te leo") + len("continúa"))
        self.assertEqual(recorded["schema_chars"], 0)
        # Raw prompts/messages must never be copied into accounting kwargs.
        self.assertNotIn("system_prompt", recorded)
        self.assertNotIn("messages", recorded)


if __name__ == "__main__":
    unittest.main()
