"""
Abstract LLM provider interface. The rest of the app only ever talks to
this interface, never directly to an SDK, so the model can be swapped later
(see app/services/llm/__init__.py).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderMetadata:
    """Non-clinical metadata returned by an LLM provider.

    Request/response bodies deliberately do not belong here. Clinical source
    text and structured results already have their own records; the usage
    layer stores only quantities needed for provenance and cost accounting.
    """

    provider: str
    requested_model: str
    response_model: str | None = None
    base_url: str | None = None
    message_id: str | None = None
    request_id: str | None = None
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Anthropic's authoritative output_tokens already INCLUDES thinking.
    # This detail is only the decomposition, useful for explaining spend.
    thinking_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_5m_input_tokens: int | None = None
    cache_creation_1h_input_tokens: int | None = None
    web_search_requests: int | None = None
    web_fetch_requests: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class StructuredAnalysisResult:
    value: dict[str, Any]
    metadata: ProviderMetadata


@dataclass(frozen=True)
class ChatResult:
    """A conversational reply plus the provenance of the call that made it."""

    text: str
    metadata: ProviderMetadata

    def strip(self) -> str:
        """So `provider.chat(...).strip()` keeps reading the way it did."""
        return self.text.strip()

    def __bool__(self) -> bool:
        return bool(self.text)


class StructuredAnalysisError(RuntimeError):
    """Safe Agent 2 failure with optional provider metadata.

    ``safe_kind`` is persisted. The provider's raw exception/body is not,
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
    """One endpoint, serving every agent.

    ``model``, ``effort`` and ``max_tokens`` are per-call overrides. Omit
    them and the call uses what the provider was constructed with.
    """

    @abstractmethod
    def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> ChatResult:
        """A conversational turn.

        ``max_tokens`` defaults to None rather than a number: a truthy
        default here would silently shadow the configured value.
        """
        raise NotImplementedError

    @abstractmethod
    def analyze_structured(
        self,
        system_prompt: str,
        user_text: str,
        tool_schema: dict[str, Any],
        *,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> StructuredAnalysisResult:
        """Return a dict matching ``tool_schema['input_schema']``."""
        raise NotImplementedError
