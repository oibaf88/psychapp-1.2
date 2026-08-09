"""
Claude (Anthropic API) implementation of LLMProvider.

Both agents run on the Anthropic API:

  * Agent 1 (conversational)      -> settings.anthropic_chat_model
  * Agent 2 (linguistic analyst)  -> settings.anthropic_analysis_model

They are separate settings so the analyst can be pinned to a
higher-capability model than the chat agent (or vice versa) without
touching code. There are no downloadable Claude weights, so these calls
always go out over the network; everything else in PsychApp (database,
deterministic risk engine, web server, frontend) runs on your own
infrastructure.
"""
import json
from typing import Any

import anthropic

from app.config import get_settings
from app.services.llm.base import LLMProvider

# Structured outputs reject the numeric/length constraint keywords that are
# valid in a tool input_schema. Drop them on conversion; the prompt and the
# risk engine already clamp the values.
_UNSUPPORTED_SCHEMA_KEYS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "pattern",
}


def _to_output_schema(node: Any) -> Any:
    """Convert a tool input_schema into a structured-outputs JSON schema.

    Every object must list all of its properties as required and set
    additionalProperties to false, otherwise the API rejects the schema.
    """
    if isinstance(node, list):
        return [_to_output_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    converted = {
        key: _to_output_schema(value)
        for key, value in node.items()
        if key not in _UNSUPPORTED_SCHEMA_KEYS
    }
    if converted.get("type") == "object" and "properties" in converted:
        converted["required"] = list(converted["properties"].keys())
        converted["additionalProperties"] = False
    return converted


class RefusalError(RuntimeError):
    """Claude's safety classifiers declined the request.

    Raised so callers fall back to their own deterministic path. The crisis
    flow in app/services/conversation.py already returns the server-owned
    safety templates whenever the LLM call raises, so a refusal degrades to
    the same safe output as a network error.
    """


class AnthropicProvider(LLMProvider):
    def __init__(self):
        settings = get_settings()
        self._chat_model = settings.anthropic_chat_model
        self._analysis_model = settings.anthropic_analysis_model
        self._chat_effort = settings.anthropic_chat_effort
        self._analysis_effort = settings.anthropic_analysis_effort
        self._max_tokens = settings.anthropic_max_tokens
        self._api_key = settings.anthropic_api_key
        self._client: anthropic.Anthropic | None = None
        if self._api_key:
            self._client = anthropic.Anthropic(api_key=self._api_key)

    def _require_client(self) -> anthropic.Anthropic:
        if self._client is None:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not configured. Set it in your environment "
                "(see .env.example) to enable Claude-powered chat and analysis. "
                "Without it, PsychApp still runs, but the conversational and "
                "linguistic-analysis features are unavailable — see README."
            )
        return self._client

    @staticmethod
    def _first_text(response: Any) -> str:
        if response.stop_reason == "refusal":
            raise RefusalError(
                f"Claude declined the request (category: "
                f"{getattr(response.stop_details, 'category', None)})."
            )
        parts = [block.text for block in response.content if block.type == "text"]
        return "\n".join(parts).strip()

    def chat(self, system_prompt: str, messages: list[dict[str, str]], max_tokens: int = 1024) -> str:
        client = self._require_client()
        response = client.messages.create(
            model=self._chat_model,
            max_tokens=max_tokens or self._max_tokens,
            output_config={"effort": self._chat_effort},
            system=system_prompt,
            messages=messages,
        )
        return self._first_text(response)

    def analyze_structured(
        self,
        system_prompt: str,
        user_text: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._require_client()
        schema = _to_output_schema(tool_schema["input_schema"])
        response = client.messages.create(
            model=self._analysis_model,
            max_tokens=self._max_tokens,
            output_config={
                "effort": self._analysis_effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
        )
        text = self._first_text(response)
        if not text:
            raise RuntimeError("Claude returned no structured analysis for the text.")
        return json.loads(text)
