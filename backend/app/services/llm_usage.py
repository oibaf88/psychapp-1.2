"""Privacy-preserving accounting for provider calls.

The clinical source text already belongs in its own tables.  This ledger is
therefore deliberately metadata-only: enough to reconcile a provider bill and
to answer *why* a request was expensive without duplicating patient content.

Writes use their own short database session.  Usage accounting must never make
a successful clinical response fail because the accounting row could not be
written; structured analysis still has its stronger fail-closed trace in
``agent2_analysis_traces``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.database import SessionLocal
from app.services.llm.base import ProviderMetadata

logger = logging.getLogger("psychapp.llm_usage")


def _agent_role(call_kind: str, max_tokens: int | None) -> str:
    """Classify current call sites without coupling the provider to routers.

    The token budget is persisted as well, so this remains auditable if a
    future call site stops matching one of the specialised budgets below.
    """
    if call_kind == "structured_analysis":
        return "analyzer_merged"
    if max_tokens == 16:
        return "endpoint_test"
    if max_tokens == 400:
        return "agent1_crisis"
    if max_tokens == 2000:
        return "agent3_copilot"
    return "agent1_chat"


def record_usage_safely(
    *,
    call_kind: str,
    metadata: ProviderMetadata,
    status: str,
    effort: str | None,
    max_tokens: int | None,
    system_chars: int | None = None,
    message_chars: int | None = None,
    schema_chars: int | None = None,
    error_kind: str | None = None,
) -> None:
    """Persist one provider attempt, swallowing accounting-only failures."""
    values: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "call_kind": call_kind,
        "agent_role": _agent_role(call_kind, max_tokens),
        "status": status,
        "provider": metadata.provider,
        "provider_base_url": metadata.base_url,
        "requested_model": metadata.requested_model,
        "response_model": metadata.response_model,
        "effort": effort,
        "max_tokens": max_tokens,
        "provider_message_id": metadata.message_id,
        "provider_request_id": metadata.request_id,
        "stop_reason": metadata.stop_reason,
        "input_tokens": metadata.input_tokens,
        "output_tokens": metadata.output_tokens,
        "thinking_tokens": metadata.thinking_tokens,
        "cache_creation_input_tokens": metadata.cache_creation_input_tokens,
        "cache_read_input_tokens": metadata.cache_read_input_tokens,
        "cache_creation_5m_input_tokens": metadata.cache_creation_5m_input_tokens,
        "cache_creation_1h_input_tokens": metadata.cache_creation_1h_input_tokens,
        "web_search_requests": metadata.web_search_requests,
        "web_fetch_requests": metadata.web_fetch_requests,
        "system_chars": system_chars,
        "message_chars": message_chars,
        "schema_chars": schema_chars,
        "latency_ms": metadata.latency_ms,
        "error_kind": error_kind,
        "created_at": datetime.now(timezone.utc),
    }
    statement = text(
        """
        insert into llm_usage_events (
            id, call_kind, agent_role, status, provider, provider_base_url,
            requested_model, response_model, effort, max_tokens,
            provider_message_id, provider_request_id, stop_reason,
            input_tokens, output_tokens, thinking_tokens,
            cache_creation_input_tokens, cache_read_input_tokens,
            cache_creation_5m_input_tokens, cache_creation_1h_input_tokens,
            web_search_requests, web_fetch_requests,
            system_chars, message_chars, schema_chars,
            latency_ms, error_kind, created_at
        ) values (
            cast(:id as uuid), :call_kind, :agent_role, :status, :provider, :provider_base_url,
            :requested_model, :response_model, :effort, :max_tokens,
            :provider_message_id, :provider_request_id, :stop_reason,
            :input_tokens, :output_tokens, :thinking_tokens,
            :cache_creation_input_tokens, :cache_read_input_tokens,
            :cache_creation_5m_input_tokens, :cache_creation_1h_input_tokens,
            :web_search_requests, :web_fetch_requests,
            :system_chars, :message_chars, :schema_chars,
            :latency_ms, :error_kind, :created_at
        )
        on conflict do nothing
        """
    )
    db = SessionLocal()
    try:
        db.execute(statement, values)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("LLM usage accounting could not be persisted: %s", type(exc).__name__)
    finally:
        db.close()
