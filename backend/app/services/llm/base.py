"""
Abstract LLM provider interface. The rest of the app only ever talks to
this interface, never directly to the Anthropic SDK, so the model can be
swapped later (see app/services/llm/__init__.py).
"""
from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, system_prompt: str, messages: list[dict[str, str]], max_tokens: int = 1024) -> str:
        """Agent 1 (conversational). `messages` is a list of {"role": "user"|"assistant", "content": str}."""
        raise NotImplementedError

    @abstractmethod
    def analyze_structured(
        self,
        system_prompt: str,
        user_text: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Agent 2 (linguistic analyst). Must return a dict matching
        tool_schema["input_schema"], forced via tool-use / function
        calling so the output is reliably parseable JSON, never free text.
        """
        raise NotImplementedError
