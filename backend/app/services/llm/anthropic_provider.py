"""Claude (Anthropic API) implementation of LLMProvider.

Every agent runs on the Anthropic API when that provider is active.  Prompt
caching is enabled at the request level because PsychDeep has unusually large,
stable prefixes (clinical system prompts, schemas, dossier/history).  Caching
changes neither the prompt nor the model response contract; it only avoids
billing the same prefix as fresh input on each nearby turn.
"""
import json
import time
from collections.abc import Callable
from typing import Any

import anthropic

from app.config import get_settings
from app.services.llm.base import (
    ChatResult,
    LLMProvider,
    ProviderMetadata,
    StructuredAnalysisError,
    StructuredAnalysisResult,
)

ANTHROPIC_API_BASE_URL = "https://api.anthropic.com"

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
    """Convert a tool input_schema into Anthropic structured-output schema."""
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


def _field(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _content_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(str(message.get("content") or "")) for message in messages)


class RefusalError(StructuredAnalysisError):
    """Claude's safety classifiers declined the request."""

    def __init__(self, *, metadata: ProviderMetadata | None = None):
        super().__init__("refused", metadata=metadata)


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        *,
        chat_model: str | None = None,
        analysis_model: str | None = None,
        copilot_model: str | None = None,
        max_tokens: int | None = None,
        usage_recorder: Callable[..., None] | None = None,
    ):
        settings = get_settings()
        self._chat_model = chat_model or settings.anthropic_chat_model
        self._analysis_model = analysis_model or settings.anthropic_analysis_model
        self._copilot_model = copilot_model or settings.copilot_model
        self._chat_effort = settings.anthropic_chat_effort
        self._analysis_effort = settings.anthropic_analysis_effort
        self._copilot_effort = settings.copilot_effort
        self._max_tokens_chat = max_tokens or settings.max_tokens_chat
        self._max_tokens_analysis = max_tokens or settings.max_tokens_analysis
        self._usage_recorder = usage_recorder
        self._api_key = settings.anthropic_api_key
        self._client: anthropic.Anthropic | None = None
        if self._api_key:
            self._client = anthropic.Anthropic(api_key=self._api_key)

    @property
    def copilot_model(self) -> str:
        return self._copilot_model

    @property
    def copilot_effort(self) -> str:
        return self._copilot_effort

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
        parts = [block.text for block in response.content if block.type == "text"]
        return "\n".join(parts).strip()

    @staticmethod
    def _metadata(response: Any, requested_model: str, latency_ms: int) -> ProviderMetadata:
        usage = getattr(response, "usage", None)
        output_details = _field(usage, "output_tokens_details")
        cache_creation = _field(usage, "cache_creation")
        server_tool_use = _field(usage, "server_tool_use")
        return ProviderMetadata(
            provider="anthropic",
            requested_model=requested_model,
            response_model=getattr(response, "model", None),
            base_url=ANTHROPIC_API_BASE_URL,
            message_id=getattr(response, "id", None),
            request_id=getattr(response, "_request_id", None),
            stop_reason=getattr(response, "stop_reason", None),
            input_tokens=_field(usage, "input_tokens"),
            output_tokens=_field(usage, "output_tokens"),
            thinking_tokens=_field(output_details, "thinking_tokens"),
            cache_creation_input_tokens=_field(usage, "cache_creation_input_tokens"),
            cache_read_input_tokens=_field(usage, "cache_read_input_tokens"),
            cache_creation_5m_input_tokens=_field(cache_creation, "ephemeral_5m_input_tokens"),
            cache_creation_1h_input_tokens=_field(cache_creation, "ephemeral_1h_input_tokens"),
            web_search_requests=_field(server_tool_use, "web_search_requests"),
            web_fetch_requests=_field(server_tool_use, "web_fetch_requests"),
            latency_ms=latency_ms,
        )

    def _record(self, **kwargs) -> None:
        if self._usage_recorder is not None:
            self._usage_recorder(**kwargs)

    def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> ChatResult:
        client = self._require_client()
        requested_model = model or self._chat_model
        token_budget = max_tokens or self._max_tokens_chat
        effective_effort = effort or self._chat_effort
        started = time.perf_counter()
        try:
            response = client.messages.create(
                model=requested_model,
                max_tokens=token_budget,
                output_config={"effort": effective_effort},
                cache_control={"type": "ephemeral"},
                system=system_prompt,
                messages=messages,
            )
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000)
            metadata = ProviderMetadata(
                provider="anthropic",
                requested_model=requested_model,
                base_url=ANTHROPIC_API_BASE_URL,
                request_id=getattr(exc, "request_id", None),
                latency_ms=latency_ms,
            )
            self._record(
                call_kind="chat",
                metadata=metadata,
                status="failed",
                effort=effective_effort,
                max_tokens=token_budget,
                system_chars=len(system_prompt),
                message_chars=_content_chars(messages),
                schema_chars=0,
                error_kind=type(exc).__name__[:64],
            )
            raise

        latency_ms = round((time.perf_counter() - started) * 1000)
        metadata = self._metadata(response, requested_model, latency_ms)
        if response.stop_reason == "refusal":
            self._record(
                call_kind="chat",
                metadata=metadata,
                status="failed",
                effort=effective_effort,
                max_tokens=token_budget,
                system_chars=len(system_prompt),
                message_chars=_content_chars(messages),
                schema_chars=0,
                error_kind="RefusalError",
            )
            raise RefusalError(metadata=metadata)
        text = self._first_text(response)
        self._record(
            call_kind="chat",
            metadata=metadata,
            status="succeeded",
            effort=effective_effort,
            max_tokens=token_budget,
            system_chars=len(system_prompt),
            message_chars=_content_chars(messages),
            schema_chars=0,
        )
        return ChatResult(text=text, metadata=metadata)

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
        requested_model = model or self._analysis_model
        token_budget = max_tokens or self._max_tokens_analysis
        effective_effort = effort or self._analysis_effort
        try:
            client = self._require_client()
        except RuntimeError:
            raise StructuredAnalysisError(
                "configuration_error",
                metadata=ProviderMetadata(
                    provider="anthropic",
                    requested_model=requested_model,
                    base_url=ANTHROPIC_API_BASE_URL,
                ),
                error_code="api_key_not_configured",
            ) from None

        schema = _to_output_schema(tool_schema["input_schema"])
        schema_chars = len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")))
        started = time.perf_counter()
        try:
            response = client.messages.create(
                model=requested_model,
                max_tokens=token_budget,
                output_config={
                    "effort": effective_effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                cache_control={"type": "ephemeral"},
                system=system_prompt,
                messages=[{"role": "user", "content": user_text}],
            )
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000)
            kind = type(exc).__name__
            lowered = kind.lower()
            if "timeout" in lowered:
                safe_kind = "timeout"
            elif "authentication" in lowered:
                safe_kind = "configuration_error"
            else:
                safe_kind = "provider_error"
            metadata = ProviderMetadata(
                provider="anthropic",
                requested_model=requested_model,
                base_url=ANTHROPIC_API_BASE_URL,
                request_id=getattr(exc, "request_id", None),
                latency_ms=latency_ms,
            )
            self._record(
                call_kind="structured_analysis",
                metadata=metadata,
                status="failed",
                effort=effective_effort,
                max_tokens=token_budget,
                system_chars=len(system_prompt),
                message_chars=len(user_text),
                schema_chars=schema_chars,
                error_kind=kind[:64],
            )
            error_body = getattr(exc, "body", None)
            error_code = None
            if isinstance(error_body, dict):
                error = error_body.get("error")
                if isinstance(error, dict) and isinstance(error.get("type"), str):
                    error_code = error["type"][:64]
            raise StructuredAnalysisError(
                safe_kind,
                metadata=metadata,
                error_code=error_code,
                http_status=getattr(exc, "status_code", None),
            ) from None

        latency_ms = round((time.perf_counter() - started) * 1000)
        metadata = self._metadata(response, requested_model, latency_ms)
        if response.stop_reason == "refusal":
            self._record(
                call_kind="structured_analysis",
                metadata=metadata,
                status="failed",
                effort=effective_effort,
                max_tokens=token_budget,
                system_chars=len(system_prompt),
                message_chars=len(user_text),
                schema_chars=schema_chars,
                error_kind="RefusalError",
            )
            raise RefusalError(metadata=metadata)

        text_value = self._first_text(response)
        self._record(
            call_kind="structured_analysis",
            metadata=metadata,
            status="succeeded",
            effort=effective_effort,
            max_tokens=token_budget,
            system_chars=len(system_prompt),
            message_chars=len(user_text),
            schema_chars=schema_chars,
        )
        if not text_value:
            raise StructuredAnalysisError("invalid_output", metadata=metadata)
        try:
            value = json.loads(text_value)
        except (json.JSONDecodeError, TypeError):
            raise StructuredAnalysisError("invalid_output", metadata=metadata) from None
        if not isinstance(value, dict):
            raise StructuredAnalysisError("invalid_output", metadata=metadata)
        return StructuredAnalysisResult(value=value, metadata=metadata)
