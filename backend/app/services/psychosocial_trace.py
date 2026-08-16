"""Persistence helpers for Agent 4 (psychosocial extraction) lineage.

Mirrors ``app/services/agent2_trace.py`` deliberately, including the
fail-closed rule: the ``started`` row is committed before the outbound call,
so an interrupted process still leaves evidence that a request left the
building. No clinical content is duplicated here — the trace links to the
chat message or diary entry, and the extracted observations link back.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.prompts import (
    AGENT4_PROMPT_VERSION,
    AGENT4_SCHEMA_VERSION,
    AGENT4_SYSTEM_PROMPT,
    AGENT4_TOOL_SCHEMA,
)
from app.models import PsychosocialExtractionTrace
from app.services.llm import ProviderMetadata, StructuredAnalysisError


class TracePersistenceError(RuntimeError):
    """A trace could not be durably written before an outbound LLM call."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _schema_sha256() -> str:
    canonical = json.dumps(AGENT4_TOOL_SCHEMA, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(canonical)


def start(
    db: Session,
    *,
    user_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    correlation_id: uuid.UUID | None = None,
) -> PsychosocialExtractionTrace:
    if source_type not in {"chat_message", "diary_entry"}:
        raise ValueError("Unsupported Agent 4 source type")

    settings = get_settings()
    now = datetime.now(timezone.utc)
    trace = PsychosocialExtractionTrace(
        id=uuid.uuid4(),
        correlation_id=correlation_id or uuid.uuid4(),
        user_id=user_id,
        source_type=source_type,
        chat_message_id=source_id if source_type == "chat_message" else None,
        diary_entry_id=source_id if source_type == "diary_entry" else None,
        status="started",
        provider="anthropic",
        requested_model=settings.anthropic_analysis_model,
        effort=settings.anthropic_analysis_effort,
        max_tokens=settings.anthropic_max_tokens,
        prompt_version=AGENT4_PROMPT_VERSION,
        prompt_sha256=_sha256_text(AGENT4_SYSTEM_PROMPT),
        schema_version=AGENT4_SCHEMA_VERSION,
        schema_sha256=_schema_sha256(),
        app_release=(os.getenv("RENDER_GIT_COMMIT") or os.getenv("APP_RELEASE") or "local")[:64],
        started_at=now,
        created_at=now,
    )
    try:
        db.add(trace)
        db.commit()
        db.refresh(trace)
    except Exception:
        db.rollback()
        raise TracePersistenceError("Agent 4 trace could not be started") from None
    return trace


def apply_metadata(trace: PsychosocialExtractionTrace, metadata: ProviderMetadata | None) -> None:
    if metadata is None:
        return
    trace.provider = metadata.provider
    trace.requested_model = metadata.requested_model
    trace.response_model = metadata.response_model
    trace.provider_message_id = metadata.message_id
    trace.provider_request_id = metadata.request_id
    trace.stop_reason = metadata.stop_reason
    trace.input_tokens = metadata.input_tokens
    trace.output_tokens = metadata.output_tokens
    trace.cache_creation_input_tokens = metadata.cache_creation_input_tokens
    trace.cache_read_input_tokens = metadata.cache_read_input_tokens
    trace.latency_ms = metadata.latency_ms


def mark_succeeded(
    trace: PsychosocialExtractionTrace,
    metadata: ProviderMetadata,
    *,
    observation_count: int,
) -> None:
    apply_metadata(trace, metadata)
    trace.status = "succeeded"
    trace.observation_count = observation_count
    trace.completed_at = datetime.now(timezone.utc)


def mark_failed(db: Session, trace: PsychosocialExtractionTrace, exc: Exception) -> None:
    """Record an allow-listed failure category. Never the provider's message.

    Raw SDK errors can echo the patient's own words back into the database
    and into hosting logs, so only the exception class name and a known
    status are persisted.
    """
    metadata = exc.metadata if isinstance(exc, StructuredAnalysisError) else None
    apply_metadata(trace, metadata)
    if isinstance(exc, StructuredAnalysisError):
        status = exc.safe_kind
        trace.error_code = exc.error_code
        trace.http_status = exc.http_status
        trace.error_kind = type(exc).__name__[:64]
    else:
        status = "invalid_output" if type(exc).__name__ == "ValidationError" else "provider_error"
        trace.error_kind = type(exc).__name__[:64]

    allowed = {
        "refused",
        "invalid_output",
        "configuration_error",
        "provider_error",
        "timeout",
        "abandoned",
    }
    trace.status = status if status in allowed else "provider_error"
    trace.completed_at = datetime.now(timezone.utc)
    try:
        db.add(trace)
        db.commit()
    except Exception:
        db.rollback()
        # The durable ``started`` row remains evidence of an interrupted
        # invocation even if finalisation fails.


def mark_stale_started_as_abandoned(db: Session, *, older_than_minutes: int = 15) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    rows = (
        db.query(PsychosocialExtractionTrace)
        .filter(
            PsychosocialExtractionTrace.status == "started",
            PsychosocialExtractionTrace.started_at < cutoff,
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        row.status = "abandoned"
        row.error_kind = "process_interrupted"
        row.completed_at = now
    if rows:
        db.commit()
    return len(rows)
