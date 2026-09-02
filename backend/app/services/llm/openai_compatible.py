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
JSON will happily return it wrapped in a `````json`` fence, prefixed with
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
import os
import re
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

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
    cleaned = _strip_code_fences(text.strip())
    try:
        val = json.loads(cleaned)
        if isinstance(val, dict):
            return val
    except ValueError:
        pass

    # Balanced bracket scanner. Simple string-aware state machine.
    in_string = False
    escape = False
    depth = 0
    start = -1

    for i, char in enumerate(text):
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if char == "{":
            if depth == 0:
                start = i
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                candidate = text[start : i + 1]
                try:
                    val = json.loads(candidate)
                    if isinstance(val, dict):
                        return val
                except ValueError:
                    # Keep scanning in case a later balanced block is valid JSON
                    start = -1

    raise ValueError("No valid JSON object found in response")


def get_candidate_base_urls(base_url: str) -> list[str]:
    """Generate candidate URLs to try for local endpoints.

    When running inside a Docker container, 'localhost' and '127.0.0.1' refer
    to the container itself, while LM Studio / Ollama run on the host machine.
    Translating 'localhost' / '127.0.0.1' to 'host.docker.internal' allows
    seamless access.

    When running on the host, IPv6 ('localhost' -> ::1) might fail or time out
    if the local server binds strictly to IPv4 (127.0.0.1). Falling back across
    variants ensures connection succeeds regardless of network setup.
    """
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return [raw]

    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return [raw]

    local_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
    if hostname not in local_hosts:
        return [raw]

    in_docker = (
        os.path.exists("/.dockerenv")
        or os.environ.get("RUNNING_IN_DOCKER") == "true"
        or os.environ.get("CONTAINER") == "true"
        or os.path.exists("/run/.containerenv")
    )

    if in_docker:
        host_order = ["host.docker.internal", "127.0.0.1", "localhost"]
    else:
        if hostname == "127.0.0.1":
            host_order = ["127.0.0.1", "localhost", "host.docker.internal"]
        elif hostname == "localhost":
            host_order = ["localhost", "127.0.0.1", "host.docker.internal"]
        elif hostname == "host.docker.internal":
            host_order = ["host.docker.internal", "127.0.0.1", "localhost"]
        else:
            host_order = [hostname, "127.0.0.1", "localhost", "host.docker.internal"]

    candidates = []
    for h in host_order:
        netloc = f"{h}:{parsed.port}" if parsed.port else h
        if parsed.username or parsed.password:
            auth = f"{parsed.username}:{parsed.password}@" if parsed.password else f"{parsed.username}@"
            netloc = f"{auth}{netloc}"
        cand = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)).rstrip("/")
        if cand not in candidates:
            candidates.append(cand)
    return candidates


def _schema_instruction(tool_schema: dict[str, Any]) -> str:
    """Append the schema to the system prompt as an unambiguous format.

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
        self._effective_base_url: str | None = None
        self._chat_model = chat_model
        self._analysis_model = analysis_model
        self._copilot_model = copilot_model or chat_model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds

    # ------------------------------------------------------------- plumbing --
    @property
    def base_url(self) -> str:
        return self._effective_base_url or self._base_url

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
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _post(self, payload: dict[str, Any], requested_model: str) -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        if self._effective_base_url:
            candidates = [self._effective_base_url]
        else:
            candidates = get_candidate_base_urls(self._base_url)

        last_exc: Exception | None = None
        body: dict[str, Any] | None = None
        response: httpx.Response | None = None
        working_url: str | None = None

        for idx, cand_url in enumerate(candidates):
            conn_timeout = 3.0 if len(candidates) > 1 and idx < len(candidates) - 1 else CONNECT_TIMEOUT_SECONDS
            timeout = httpx.Timeout(self._timeout_seconds, connect=conn_timeout)
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(
                        f"{cand_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )
                response = resp
                working_url = cand_url
                break
            except (httpx.ConnectTimeout, httpx.ConnectError, httpx.NetworkError) as exc:
                last_exc = exc
                logger.debug("Failed connecting to local candidate %s: %s", cand_url, exc)
                continue
            except httpx.ReadTimeout:
                raise StructuredAnalysisError(
                    "timeout",
                    metadata=self._error_metadata(requested_model, started, base_url=cand_url),
                    error_code="local_endpoint_timeout",
                ) from None
            except httpx.TimeoutException as exc:
                last_exc = exc
                continue
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.debug("HTTP error connecting to local candidate %s: %s", cand_url, exc)
                continue

        if response is None:
            if isinstance(last_exc, httpx.TimeoutException):
                raise StructuredAnalysisError(
                    "timeout",
                    metadata=self._error_metadata(requested_model, started),
                    error_code="local_endpoint_timeout",
                ) from None
            raise StructuredAnalysisError(
                "provider_error",
                metadata=self._error_metadata(requested_model, started),
                error_code="local_endpoint_unreachable",
            ) from None

        if working_url:
            self._effective_base_url = working_url

        latency_ms = round((time.perf_counter() - started) * 1000)
        current_base_url = self.base_url
        if response.status_code >= 400:
            safe_kind = "configuration_error" if response.status_code in (401, 403, 404) else "provider_error"
            raise StructuredAnalysisError(
                safe_kind,
                metadata=ProviderMetadata(
                    provider=PROVIDER_NAME,
                    requested_model=requested_model,
                    base_url=current_base_url,
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
                    base_url=current_base_url,
                    latency_ms=latency_ms,
                ),
                error_code="non_json_response",
            ) from None
        return body, latency_ms

    def _error_metadata(self, requested_model: str, started: float, base_url: str | None = None) -> ProviderMetadata:
        return ProviderMetadata(
            provider=PROVIDER_NAME,
            requested_model=requested_model,
            base_url=base_url or self.base_url,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    @staticmethod
    def _metadata(body: dict[str, Any], requested_model: str, base_url: str, latency_ms: int) -> ProviderMetadata:
        usage = body.get("usage") or {}
        choices = body.get("choices") or [{}]
        return ProviderMetadata(
            provider=PROVIDER_NAME,
            requested_model=requested_model,
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
        effort: str | None = None,
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
            metadata=self._metadata(body, requested_model, self.base_url, latency_ms),
        )

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
        payload = {
            "model": requested_model,
            "max_tokens": max_tokens or self._max_tokens,
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

        metadata = self._metadata(body, requested_model, self.base_url, latency_ms)
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
