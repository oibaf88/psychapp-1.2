"""
Claude (Anthropic API) implementation of LLMProvider.

Every agent runs on the Anthropic API:

  * Agent 1 (conversational)  -> settings.anthropic_chat_model
  * The analyst               -> settings.anthropic_analysis_model
  * Agent 3 (clinical copilot)-> settings.anthropic_copilot_model

They are separate settings so each can be pinned to what its job needs
without touching code. A caller may also pass ``model``/``effort`` for one
call, which is how the copilot gets its own model without a second client
and a second resolution of the active endpoint.

There are no downloadable Claude weights, so these calls always go out over
the network; everything else in PsychApp (database, deterministic risk
engine, web server, frontend) runs on your own infrastructure.
"""
import json
import time
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

# Recorded on every trace so a historic analysis says where it was computed,
# not merely which model name was requested.
ANTHROPIC_API_BASE_URL = "https://api.anthropic.com"

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


class RefusalError(StructuredAnalysisError):
    """Claude's safety classifiers declined the request.

    Raised so callers fall back to their own deterministic path. The crisis
    flow in app/services/conversation.py already returns the server-owned
    safety templates whenever the LLM call raises, so a refusal degrades to
    the same safe output as a network error.
    """

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
    ):
        # The models are overridable so a runtime configuration can pin them
        # without a redeploy; everything else stays deployment-level, because
        # the API key and the effort settings are not a user-facing choice.
        settings = get_settings()
        self._chat_model = chat_model or settings.anthropic_chat_model
        self._analysis_model = analysis_model or settings.anthropic_analysis_model
        self._copilot_model = copilot_model or settings.copilot_model
        self._chat_effort = settings.anthropic_chat_effort
        self._analysis_effort = settings.anthropic_analysis_effort
        self._copilot_effort = settings.copilot_effort
        # A runtime configuration carries one token budget, so it stands in
        # for both roles when set; otherwise each keeps its own.
        self._max_tokens_chat = max_tokens or settings.max_tokens_chat
        self._max_tokens_analysis = max_tokens or settings.max_tokens_analysis
        self._api_key = settings.anthropic_api_key
        self._client: anthropic.Anthropic | None = None
        if self._api_key:
            self._client = anthropic.Anthropic(api_key=self._api_key)

    # The copilot's settings, exposed so its call site can ask for them
    # without reaching into `get_settings()` itself — and so the same call
    # site works against either provider.
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
        if response.stop_reason == "refusal":
            raise RefusalError()
        parts = [block.text for block in response.content if block.type == "text"]
        return "\n".join(parts).strip()

    @staticmethod
    def _metadata(response: Any, requested_model: str, latency_ms: int) -> ProviderMetadata:
        usage = getattr(response, "usage", None)
        return ProviderMetadata(
            provider="anthropic",
            requested_model=requested_model,
            response_model=getattr(response, "model", None),
            base_url=ANTHROPIC_API_BASE_URL,
            message_id=getattr(response, "id", None),
            request_id=getattr(response, "_request_id", None),
            stop_reason=getattr(response, "stop_reason", None),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None),
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None),
            latency_ms=latency_ms,
        )

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
        started = time.perf_counter()
        response = client.messages.create(
            model=requested_model,
            max_tokens=max_tokens or self._max_tokens_chat,
            output_config={"effort": effort or self._chat_effort},
            system=system_prompt,
            messages=messages,
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        # _first_text raises RefusalError before this returns, which callers
        # already treat as "no reply" — unchanged.
        text = self._first_text(response)
        return ChatResult(text=text, metadata=self._metadata(response, requested_model, latency_ms))

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
        started = time.perf_counter()
        try:
            response = client.messages.create(
                model=requested_model,
                max_tokens=max_tokens or self._max_tokens_analysis,
                output_config={
                    "effort": effort or self._analysis_effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
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
            raise RefusalError(metadata=metadata)
        text = self._first_text(response)
        if not text:
            raise StructuredAnalysisError("invalid_output", metadata=metadata)
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            raise StructuredAnalysisError("invalid_output", metadata=metadata) from None
        if not isinstance(value, dict):
            raise StructuredAnalysisError("invalid_output", metadata=metadata)
        return StructuredAnalysisResult(value=value, metadata=metadata)
