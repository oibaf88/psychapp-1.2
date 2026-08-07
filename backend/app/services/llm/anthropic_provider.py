"""
Claude (Anthropic API) implementation of LLMProvider.

Important limitation, called out explicitly per the project brief: there
are no downloadable Claude model weights, so this can never run fully
offline. This provider calls the public Anthropic Messages API over the
network and requires ANTHROPIC_API_KEY to be set (see ../../../.env.example).
Everything else in PsychApp (database, deterministic risk engine, web
server, frontend) runs entirely on your machine.
"""
from typing import Any

import anthropic

from app.config import get_settings
from app.services.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(self):
        settings = get_settings()
        self._model = settings.anthropic_model
        self._max_tokens = settings.anthropic_max_tokens
        self._api_key = settings.anthropic_api_key
        self._client: anthropic.Anthropic | None = None
        if self._api_key:
            self._client = anthropic.Anthropic(api_key=self._api_key)

    def _require_client(self) -> anthropic.Anthropic:
        if self._client is None:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not configured. Set it in your .env file "
                "(see .env.example) to enable Claude-powered chat and analysis. "
                "Without it, PsychApp still runs, but the conversational and "
                "linguistic-analysis features are unavailable — see README."
            )
        return self._client

    def chat(self, system_prompt: str, messages: list[dict[str, str]], max_tokens: int = 1024) -> str:
        client = self._require_client()
        response = client.messages.create(
            model=self._model,
            max_tokens=max_tokens or self._max_tokens,
            system=system_prompt,
            messages=messages,
        )
        text_parts = [block.text for block in response.content if block.type == "text"]
        return "\n".join(text_parts).strip()

    def analyze_structured(
        self,
        system_prompt: str,
        user_text: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any]:
        client = self._require_client()
        response = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_schema["name"]},
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_schema["name"]:
                return dict(block.input)
        raise RuntimeError("Claude did not return the expected tool_use block for structured analysis.")
