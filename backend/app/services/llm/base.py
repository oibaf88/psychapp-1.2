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
    # Where the call actually went. Recorded because "which model" is not
    # answerable without it once the endpoint is configurable: two
    # deployments can both report "llama-3.1-8b" and mean different weights.
    base_url: str | None = None
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


@dataclass(frozen=True)
class ChatResult:
    """A conversational reply plus the provenance of the call that made it.

    ``chat()`` used to return a bare string, so an assistant turn recorded
    the model the app *asked* for rather than the one the server said
    answered. On a hosted API those agree; on a local runtime they routinely
    do not, which is exactly the case the provenance exists for.
    """

    text: str
    metadata: ProviderMetadata

    def strip(self) -> str:
        """So `provider.chat(...).strip()` keeps reading the way it did."""
        return self.text.strip()

    def __bool__(self) -> bool:
        return bool(self.text)


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
    """One endpoint, serving every agent.

    ``model``, ``effort`` and ``max_tokens`` are per-call overrides. Omit
    them and the call uses what the provider was constructed with — the
    behaviour every existing call site relies on. They exist because the
    three agents want different settings while sharing one endpoint, and
    building three providers to say so would mean three clients, three
    resolutions of the active configuration, and three chances for them to
    disagree about which endpoint is in force.
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
        """A conversational turn. `messages` is [{"role": "user"|"assistant", "content": str}].

        ``max_tokens`` defaults to None rather than a number: a truthy
        default here silently shadowed the configured value for every caller
        that did not pass one.
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
        """
        The analytic agent. Must return a dict matching
        tool_schema["input_schema"], forced via structured outputs /
        function calling so the result is reliably parseable JSON, never
        free text.
        """
        raise NotImplementedError
