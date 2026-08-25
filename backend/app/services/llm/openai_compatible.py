"""
Provider for a locally hosted model exposed over the OpenAI chat API.

Why this exists
---------------
Claude has no downloadable weights, so every PsychApp deployment that used
the Anthropic provider sent clinical text off the machine. Running a model
you host yourself — llama.cpp's server, Ollama, LM Studio, vLLM, LocalAI —
keeps that text inside your own network, and lets you see how the app
behaves on a model you can actually inspect.

All of those servers speak the same dialect: ``POST {base_url}/chat/completions``
with ``{"model": ..., "messages": [...]}``. That de-facto standard is what
this provider targets, rather than any one product.

What is deliberately different from the Anthropic provider
----------------------------------------------------------
**Structured output is not assumed.** The Anthropic provider can demand a
JSON schema and rely on the API to honour it. A local 8B model asked for
JSON will happily return it wrapped in a ``​```json`` fence, prefixed with
"Sure! Here's the JSON:", or followed by an explanation. Refusing all of
that would make the analytic agents fail constantly on exactly the setups
this provider exists to support. So the request asks for JSON three ways —
``response_format`` with the schema, ``response_format`` as plain
``json_object``, and the schema restated in the system prompt — and the
response is then parsed leniently by :func:`extract_json_object`.

Leniency stops at the parse. The dictionary that comes out still has to
satisfy the same strict Pydantic model as any Anthropic response, with
``extra="forbid"`` and ``strict=True``; a local model cannot smuggle an
extra field or an out-of-range score past the boundary. What differs is
only how much punctuation we are willing to look through to find the
object, never what counts as a valid object.

**Nothing here loosens a clinical guarantee.** The deterministic risk
engine never calls a model. A weaker model can miss a linguistic marker —
which is a real, disclosed cost of running one, surfaced in the UI — but it
cannot invent an alert level, and the alert cascade behaves identically
whichever provider produced the inference.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from app.services.llm.base import (
    ChatResult,
    LLMProvider,
    ProviderMetadata,
    StructuredAnalysisError,
    StructuredAnalysisResult,
)

logger = logging.getLogger("psychapp.llm.local")

PROVIDER_NAME = "openai_compatible"

# Local servers are slower than a hosted API — a 7B model on CPU can take a
# while for a long diary entry — but the patient is waiting on Agent 1, so
# this cannot be unbounded.
DEFAULT_TIMEOUT_SECONDS = 120.0
CONNECT_TIMEOUT_SECONDS = 10.0


def _strip_code_fences(text: str) -> str:
    fenced = re.match(r"^\s*```(?:json|JSON)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    return fenced.group(1) if fenced else text


def extract_json_object(text: str) -> dict[str, Any]:
    """Recover the JSON object from a local model's reply.

    Tries the whole string, then the string minus a markdown fence, then the
    first balanced ``{...}`` span found by scanning while respecting string
    literals and escapes. A regex cannot do the last step correctly, and
    getting it wrong on a nested object is how you silently truncate an
    observation list.

    Raises ``ValueError`` when no object can be recovered.
    """
    for candidate in (text, _strip_code_fences(text)):
        stripped = candidate.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            pass
        else:
            if isinstance(parsed, dict):
                return parsed

    source = _strip_code_fences(text)
    start = source.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(source)):
            char = source[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(source[start : index + 1])
                    except (json.JSONDecodeError, TypeError):
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = source.find("{", start + 1)

    raise ValueError("no JSON object found in model output")


def _schema_instruction(tool_schema: dict[str, Any]) -> str:
    """Restate the contract in the prompt, for servers that ignore response_format.

    Many local runtimes accept ``response_format`` and quietly do nothing
    with it. Putting the schema in the system prompt is the only instruction
    every one of them actually reads.
    """
    schema = json.dumps(tool_schema["input_schema"], ensure_ascii=False, indent=2)
    return (
        "\n\n---\n"
        "Devuelve ÚNICAMENTE un objeto JSON válido que cumpla exactamente este JSON Schema. "
        "Sin texto antes ni después, sin explicaciones y sin marcas de código.\n\n"
        f"{schema}"
    )


class OpenAICompatibleProvider(LLMProvider):
    """Talks to any server exposing the OpenAI chat-completions API."""

    def __init__(
        self,
        *,
        base_url: str,
        chat_model: str,
        analysis_model: str,
        copilot_model: str = "",
        api_key: str = "",
        max_tokens: int = 4096,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._base_url = base_url.rstrip("/")
        self._chat_model = chat_model
        self._analysis_model = analysis_model
        self._copilot_model = copilot_model or chat_model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._timeout = httpx.Timeout(timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS)

    # ------------------------------------------------------------- plumbing --
    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def copilot_model(self) -> str:
        return self._copilot_model

    @property
    def copilot_effort(self) -> str:
        """No local runtime implements Anthropic's effort control.

        Returned empty rather than guessed at, so the call site can pass it
        through unconditionally and this provider simply ignores it.
        """
        return ""

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        # Most local servers ignore auth entirely; some (vLLM behind a proxy,
        # LiteLLM) require it. Sent only when the operator configured one.
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _post(self, payload: dict[str, Any], requested_model: str) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.TimeoutException:
            raise StructuredAnalysisError(
                "timeout",
                metadata=self._error_metadata(requested_model, started),
                error_code="local_endpoint_timeout",
            ) from None
        except httpx.HTTPError:
            # Wrong URL, server down, TLS problem, DNS. The operator needs to
            # know it was the endpoint, not the model.
            raise StructuredAnalysisError(
                "provider_error",
                metadata=self._error_metadata(requested_model, started),
                error_code="local_endpoint_unreachable",
            ) from None

        latency_ms = round((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            safe_kind = "configuration_error" if response.status_code in (401, 403, 404) else "provider_error"
            raise StructuredAnalysisError(
                safe_kind,
                metadata=ProviderMetadata(
                    provider=PROVIDER_NAME,
                    requested_model=requested_model,
                    base_url=self._base_url,
                    latency_ms=latency_ms,
                ),
                error_code=f"http_{response.status_code}",
                http_status=response.status_code,
            )
        try:
            body = response.json()
        except ValueError:
            raise StructuredAnalysisError(
                "invalid_output",
                metadata=ProviderMetadata(
                    provider=PROVIDER_NAME,
                    requested_model=requested_model,
                    base_url=self._base_url,
                    latency_ms=latency_ms,
                ),
                error_code="non_json_response",
            ) from None
        return body, latency_ms

    def _error_metadata(self, requested_model: str, started: float) -> ProviderMetadata:
        return ProviderMetadata(
            provider=PROVIDER_NAME,
            requested_model=requested_model,
            base_url=self._base_url,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    @staticmethod
    def _metadata(body: dict[str, Any], requested_model: str, base_url: str, latency_ms: int) -> ProviderMetadata:
        usage = body.get("usage") or {}
        choices = body.get("choices") or [{}]
        return ProviderMetadata(
            provider=PROVIDER_NAME,
            requested_model=requested_model,
            # The server reports which model actually answered. On a local
            # runtime this is the only way to notice that the loaded weights
            # are not the ones the operator thinks they configured.
            response_model=body.get("model") or requested_model,
            base_url=base_url,
            message_id=body.get("id"),
            stop_reason=(choices[0] or {}).get("finish_reason"),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
        )

    @staticmethod
    def _first_message(body: dict[str, Any]) -> str:
        choices = body.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        # Some servers return the multimodal content-part array.
        if isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict)]
            return "\n".join(parts).strip()
        return ""

    # ---------------------------------------------------------------- agents --
    def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        *,
        model: str | None = None,
        effort: str | None = None,  # noqa: ARG002 — no local runtime has one
    ) -> ChatResult:
        requested_model = model or self._chat_model
        payload = {
            "model": requested_model,
            "max_tokens": max_tokens or self._max_tokens,
            "messages": [{"role": "system", "content": system_prompt}, *messages],
        }
        body, latency_ms = self._post(payload, requested_model)
        return ChatResult(
            text=self._first_message(body),
            metadata=self._metadata(body, requested_model, self._base_url, latency_ms),
        )

    def analyze_structured(
        self,
        system_prompt: str,
        user_text: str,
        tool_schema: dict[str, Any],
        *,
        model: str | None = None,
        effort: str | None = None,  # noqa: ARG002 — no local runtime has one
        max_tokens: int | None = None,
    ) -> StructuredAnalysisResult:
        requested_model = model or self._analysis_model
        payload = {
            "model": requested_model,
            "max_tokens": max_tokens or self._max_tokens,
            # Deterministic decoding: this is an extraction task, and two
            # different readings of the same sentence would make a historic
            # decision impossible to reproduce.
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt + _schema_instruction(tool_schema)},
                {"role": "user", "content": user_text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": tool_schema.get("name", "structured_output"),
                    "schema": tool_schema["input_schema"],
                    "strict": True,
                },
            },
        }
        try:
            body, latency_ms = self._post(payload, requested_model)
        except StructuredAnalysisError as exc:
            # Servers that do not implement json_schema reject the whole
            # request rather than ignoring the field. Retry once in the
            # dialect every one of them supports; the schema is still in the
            # system prompt, and the strict Pydantic boundary is unchanged.
            if exc.http_status not in (400, 422):
                raise
            logger.info("Local endpoint rejected json_schema; retrying with json_object")
            payload["response_format"] = {"type": "json_object"}
            try:
                body, latency_ms = self._post(payload, requested_model)
            except StructuredAnalysisError as retry_exc:
                if retry_exc.http_status not in (400, 422):
                    raise
                logger.info("Local endpoint rejected json_object too; retrying as plain text")
                payload.pop("response_format", None)
                body, latency_ms = self._post(payload, requested_model)

        metadata = self._metadata(body, requested_model, self._base_url, latency_ms)
        text = self._first_message(body)
        if not text:
            raise StructuredAnalysisError("invalid_output", metadata=metadata, error_code="empty_response")
        try:
            value = extract_json_object(text)
        except ValueError:
            raise StructuredAnalysisError(
                "invalid_output", metadata=metadata, error_code="unparseable_json"
            ) from None
        return StructuredAnalysisResult(value=value, metadata=metadata)
