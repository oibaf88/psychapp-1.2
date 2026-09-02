"""Persistence helpers for privacy-preserving structured-analysis lineage.

Discriminated by ``agent_role``, which pins its own prompt and schema
version so a historic trace records the exact contract that produced it.

New traces all use ``analyzer_merged``: the linguistic and psychosocial
reads are one call. The two older roles stay registered because rows
carrying them are already in the database and must keep resolving to the
contract that produced them — they are history, not options.
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
    AGENT2_PROMPT_VERSION,
    AGENT2_SCHEMA_VERSION,
    AGENT2_SYSTEM_PROMPT,
    AGENT2_TOOL_SCHEMA,
    AGENT4_PROMPT_VERSION,
    AGENT4_SCHEMA_VERSION,
    AGENT4_SYSTEM_PROMPT,
    AGENT4_TOOL_SCHEMA,
    ANALYZER_PROMPT_VERSION,
    ANALYZER_SCHEMA_VERSION,
    ANALYZER_SYSTEM_PROMPT,
    ANALYZER_TOOL_SCHEMA,
)
from app.models import Agent2AnalysisTrace
from app.services import llm_config
from app.services.llm import ProviderMetadata, StructuredAnalysisError

# The role every new trace carries.
ANALYZER_ROLE = "analyzer_merged"

# Roles whose traces carry a linguistic reading. The merged analyser
# produces one, so the therapist's Agent 2 lineage views must include it or
# they go quietly empty the day this ships — the traces would still be
# written, just filtered out of every screen that shows them.
LINGUISTIC_ROLES = (ANALYZER_ROLE, "agent2_linguistic")

# Each role pins its own prompt and schema so a historic trace records
# exactly which contract produced it. The two agent* entries are retired —
# nothing starts a trace with them any more — but they stay here so the rows
# that already carry them keep resolving.
AGENT_CONTRACTS = {
    ANALYZER_ROLE: (
        ANALYZER_PROMPT_VERSION,
        ANALYZER_SYSTEM_PROMPT,
        ANALYZER_SCHEMA_VERSION,
        ANALYZER_TOOL_SCHEMA,
    ),
    "agent2_linguistic": (AGENT2_PROMPT_VERSION, AGENT2_SYSTEM_PROMPT, AGENT2_SCHEMA_VERSION, AGENT2_TOOL_SCHEMA),
    "agent4_psychosocial": (AGENT4_PROMPT_VERSION, AGENT4_SYSTEM_PROMPT, AGENT4_SCHEMA_VERSION, AGENT4_TOOL_SCHEMA),
}


class TracePersistenceError(RuntimeError):
    """A trace could not be durably written before an outbound LLM call."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _schema_sha256(tool_schema: dict = AGENT2_TOOL_SCHEMA) -> str:
    canonical = json.dumps(tool_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(canonical)


def start(
    db: Session,
    *,
    user_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    correlation_id: uuid.UUID | None = None,
    agent_role: str = ANALYZER_ROLE,
) -> Agent2AnalysisTrace:
    """Commit ``started`` before contacting Anthropic.

    Failing closed here prevents an external request which the application
    cannot later account for.  The deterministic risk engine remains
    available and is invoked by the caller even when this raises.
    """

    if source_type not in {"chat_message", "diary_entry"}:
        raise ValueError("Unsupported structured-analysis source type")
    if agent_role not in AGENT_CONTRACTS:
        raise ValueError(f"Unknown structured-analysis agent role: {agent_role}")

    prompt_version, system_prompt, schema_version, tool_schema = AGENT_CONTRACTS[agent_role]
    settings = get_settings()
    active = llm_config.resolve(db)
    now = datetime.now(timezone.utc)
    trace = Agent2AnalysisTrace(
        id=uuid.uuid4(),
        correlation_id=correlation_id or uuid.uuid4(),
        agent_role=agent_role,
        user_id=user_id,
        source_type=source_type,
        chat_message_id=source_id if source_type == "chat_message" else None,
        diary_entry_id=source_id if source_type == "diary_entry" else None,
        status="started",
        # Provisional: recorded before the call so a trace that never comes
        # back still says which endpoint it was aimed at. `apply_metadata`
        # replaces these with what the provider actually reported.
        provider=active.provider,
        provider_base_url=active.base_url,
        requested_model=active.analysis_model,
        effort=settings.anthropic_analysis_effort if active.provider == "anthropic" else "n/a",
        max_tokens=active.max_tokens,
        prompt_version=prompt_version,
        prompt_sha256=_sha256_text(system_prompt),
        schema_version=schema_version,
        schema_sha256=_schema_sha256(tool_schema),
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
        raise TracePersistenceError(f"{agent_role} trace could not be started") from None
    return trace


def apply_metadata(trace: Agent2AnalysisTrace, metadata: ProviderMetadata | None) -> None:
    if metadata is None:
        return
    trace.provider = metadata.provider
    trace.provider_base_url = metadata.base_url
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


def mark_succeeded(trace: Agent2AnalysisTrace, metadata: ProviderMetadata) -> None:
    apply_metadata(trace, metadata)
    trace.status = "succeeded"
    trace.completed_at = datetime.now(timezone.utc)


def mark_failed(db: Session, trace: Agent2AnalysisTrace, exc: Exception) -> None:
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
        db.query(Agent2AnalysisTrace)
        .filter(Agent2AnalysisTrace.status == "started", Agent2AnalysisTrace.started_at < cutoff)
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
