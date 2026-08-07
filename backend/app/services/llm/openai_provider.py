import os
import json
from typing import Any
import requests

from app.config import get_settings
from app.services.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    """
    OpenAI implementation of LLMProvider.
    Can be pointed to standard OpenAI API or local models like vLLM, LM Studio, Ollama
    using the OPENAI_API_BASE config option.
    """
    def __init__(self):
        settings = get_settings()
        # For OpenAI or any local server providing an OpenAI compatible API
        self._api_key = os.getenv("OPENAI_API_KEY", "not-needed-for-local")
        self._model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self._max_tokens = os.getenv("OPENAI_MAX_TOKENS", 1024)
        self._base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")

    def chat(self, system_prompt: str, messages: list[dict[str, str]], max_tokens: int = 1024) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

        # Convert anthropic style to openai style
        oai_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            oai_messages.append({"role": msg["role"], "content": msg["content"]})

        data = {
            "model": self._model,
            "messages": oai_messages,
            "max_tokens": int(max_tokens) or int(self._max_tokens)
        }

        response = requests.post(f"{self._base_url}/chat/completions", headers=headers, json=data)
        response.raise_for_status()

        result = response.json()
        return result["choices"][0]["message"]["content"].strip()

    def analyze_structured(
        self,
        system_prompt: str,
        user_text: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any]:

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]

        # Convert Anthropic tool schema to OpenAI tool schema
        # Anthropic format: {"name": "x", "description": "y", "input_schema": {"type": "object", "properties": {}}}
        # OpenAI format: {"type": "function", "function": {"name": "x", "description": "y", "parameters": {"type": "object", "properties": {}}}}
        openai_tool = {
            "type": "function",
            "function": {
                "name": tool_schema["name"],
                "description": tool_schema.get("description", ""),
                "parameters": tool_schema.get("input_schema", {"type": "object", "properties": {}})
            }
        }

        data = {
            "model": self._model,
            "messages": messages,
            "tools": [openai_tool],
            "tool_choice": {"type": "function", "function": {"name": tool_schema["name"]}},
            "max_tokens": int(self._max_tokens)
        }

        response = requests.post(f"{self._base_url}/chat/completions", headers=headers, json=data)
        response.raise_for_status()

        result = response.json()
        choice = result["choices"][0]["message"]

        if "tool_calls" in choice and choice["tool_calls"]:
            args_str = choice["tool_calls"][0]["function"]["arguments"]
            return json.loads(args_str)

        raise RuntimeError("OpenAI did not return the expected tool call for structured analysis.")
