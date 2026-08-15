"""
Abstract LLM provider interface. The rest of the app only ever talks to
this interface, never directly to the Anthropic SDK, so the model can be
swapped later (see app/services/llm/__init__.py).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderMetadata:
    """Non-clinical metadata returned by an LLM provider.

    Request/response bodies deliberately do not belong here.  Agent 2's
    source text and structured result already have their own clinical
    records; the trace layer links to those records instead of duplicating
    sensitive content.
    """

    provider: str
    requested_model: str
    response_model: str | None = None
    message_id: str | None = None
    request_id: str | None = None
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class StructuredAnalysisResult:
    value: dict[str, Any]
    metadata: ProviderMetadata


class StructuredAnalysisError(RuntimeError):
    """Safe Agent 2 failure with optional provider metadata.

    ``safe_kind`` is persisted.  The provider's raw exception/body is not,
    because it may echo clinical input or authentication material.
    """

    def __init__(
        self,
        safe_kind: str,
        *,
        metadata: ProviderMetadata | None = None,
        error_code: str | None = None,
        http_status: int | None = None,
    ):
        super().__init__(safe_kind)
        self.safe_kind = safe_kind
        self.metadata = metadata
        self.error_code = error_code
        self.http_status = http_status


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
    ) -> StructuredAnalysisResult:
        """
        Agent 2 (linguistic analyst). Must return a dict matching
        tool_schema["input_schema"], forced via tool-use / function
        calling so the output is reliably parseable JSON, never free text.
        """
        raise NotImplementedError
