"""
Which model serves this deployment, and how that choice is recorded.

The deployment default comes from the environment (``ANTHROPIC_*``). This
module lets it be overridden at runtime by a row in ``llm_endpoint_configs``,
so an operator can point Agent 1 and Agent 2 at a model they run themselves
and see how the app behaves on it, without redeploying.

Two properties matter more than the convenience:

**Every interaction records which model produced it.** The active
configuration is resolved once per call and travels with the result as
``ProviderMetadata`` — provider, requested model, the model the server said
answered, and the endpoint. That is what makes a patient's history readable
after the endpoint changed: an analysis from March under Claude and one from
April under a local Llama are both legible, and distinguishable, because
each carries its own provenance rather than inheriting today's setting.

**Changing it is an audited act.** Rows are never updated in place. Setting
a new configuration deactivates the previous one and inserts a new row, so
the sequence of "what was serving this app, and when" is reconstructable.

Caching
-------
Resolution is cached in-process and invalidated on write. The cache is
per-worker, so a change made on one worker reaches the others within
``CACHE_TTL_SECONDS`` rather than instantly — acceptable for a setting an
operator changes deliberately, and far cheaper than a database read before
every model call.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LLMEndpointConfig

logger = logging.getLogger("psychapp.llm_config")

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_LOCAL = "openai_compatible"
PROVIDERS = (PROVIDER_ANTHROPIC, PROVIDER_LOCAL)

CACHE_TTL_SECONDS = 30.0

MAX_TOKENS_MIN, MAX_TOKENS_MAX = 256, 32768
TIMEOUT_MIN, TIMEOUT_MAX = 5, 600


@dataclass(frozen=True)
class ResolvedConfig:
    """The configuration in force for one call, whatever its source."""

    provider: str
    chat_model: str
    analysis_model: str
    # Agent 3. Empty means "whatever the conversational agent uses", which is
    # what it did before it had a setting; `_from_row` and `environment_config`
    # both resolve it, so readers never have to apply the fallback themselves.
    copilot_model: str = ""
    base_url: str | None = None
    api_key: str = ""
    max_tokens: int = 4096
    timeout_seconds: int = 120
    label: str = ""
    source: str = "environment"  # environment | runtime
    config_id: str | None = None
    updated_at: datetime | None = None

    @property
    def is_local(self) -> bool:
        return self.provider == PROVIDER_LOCAL

    def public_dict(self) -> dict:
        """Everything the UI may see. Never the key."""
        return {
            "provider": self.provider,
            "provider_label": (
                "Claude (API oficial de Anthropic)" if self.provider == PROVIDER_ANTHROPIC else "Modelo propio (API compatible con OpenAI)"
            ),
            "label": self.label,
            "base_url": self.base_url,
            "chat_model": self.chat_model,
            "analysis_model": self.analysis_model,
            "copilot_model": self.copilot_model or self.chat_model,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "source": self.source,
            "config_id": self.config_id,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "has_api_key": bool(self.api_key),
        }


class LLMConfigError(ValueError):
    """The submitted configuration cannot be used."""


# ----------------------------------------------------------------- cache ---
_lock = threading.Lock()
_cached: tuple[float, ResolvedConfig] | None = None


def invalidate_cache() -> None:
    global _cached
    with _lock:
        _cached = None


def environment_config() -> ResolvedConfig:
    """The deployment default, from environment variables."""
    settings = get_settings()
    return ResolvedConfig(
        provider=PROVIDER_ANTHROPIC,
        chat_model=settings.anthropic_chat_model,
        analysis_model=settings.anthropic_analysis_model,
        copilot_model=settings.copilot_model,
        max_tokens=settings.anthropic_max_tokens,
        label="Configuración del despliegue",
        source="environment",
    )


def _from_row(row: LLMEndpointConfig) -> ResolvedConfig:
    return ResolvedConfig(
        provider=row.provider,
        chat_model=row.chat_model,
        analysis_model=row.analysis_model,
        copilot_model=row.copilot_model or row.chat_model,
        base_url=row.base_url,
        api_key=row.api_key or "",
        max_tokens=row.max_tokens,
        timeout_seconds=row.timeout_seconds,
        label=row.label or "",
        source="runtime",
        config_id=str(row.id),
        updated_at=row.created_at,
    )


def active_row(db: Session) -> LLMEndpointConfig | None:
    return (
        db.query(LLMEndpointConfig)
        .filter(LLMEndpointConfig.is_active == True)  # noqa: E712
        .order_by(LLMEndpointConfig.created_at.desc())
        .first()
    )


def resolve(db: Session | None = None) -> ResolvedConfig:
    """The configuration in force right now.

    Falls back to the environment whenever the override is switched off, no
    row exists, or the lookup fails. Failing back rather than failing hard is
    deliberate: a misconfigured optional feature must not take the
    conversational agent down with it.
    """
    global _cached
    settings = get_settings()
    if not settings.llm_allow_runtime_override:
        return environment_config()

    now = time.monotonic()
    with _lock:
        if _cached and now - _cached[0] < CACHE_TTL_SECONDS:
            return _cached[1]

    if db is None:
        # No session to hand: keep whatever was last resolved rather than
        # opening one from inside a provider constructor.
        with _lock:
            if _cached:
                return _cached[1]
        return environment_config()

    try:
        row = active_row(db)
        config = _from_row(row) if row else environment_config()
    except Exception:  # noqa: BLE001
        logger.exception("Could not read the active LLM configuration; using the environment default")
        return environment_config()

    with _lock:
        _cached = (now, config)
    return config


# ------------------------------------------------------------ validation ---
def normalise_base_url(raw: str) -> str:
    """Accept what people actually paste, reject what cannot work.

    Local runtimes are usually given as ``http://localhost:11434`` (Ollama),
    ``http://localhost:1234/v1`` (LM Studio) or ``http://127.0.0.1:8080/v1``
    (llama.cpp). The provider appends ``/chat/completions``, so the stored
    value is normalised to end at ``/v1``.
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise LLMConfigError("Escribe la URL del servidor.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise LLMConfigError("La URL tiene que empezar por http:// o https://")
    if not parsed.netloc:
        raise LLMConfigError("La URL no incluye un servidor.")
    # A path of /chat/completions means they pasted the full endpoint.
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    if not urlparse(url).path.rstrip("/"):
        # Bare host: assume the near-universal /v1 prefix.
        url = f"{url}/v1"
    return url


def validate(
    *,
    provider: str,
    base_url: str | None,
    chat_model: str,
    analysis_model: str,
    max_tokens: int,
    timeout_seconds: int,
    copilot_model: str | None = None,
) -> dict:
    if provider not in PROVIDERS:
        raise LLMConfigError(f"Proveedor no soportado: {provider}")
    if not chat_model.strip() or not analysis_model.strip():
        raise LLMConfigError("Indica el nombre del modelo para el chat y para el análisis.")
    if not MAX_TOKENS_MIN <= max_tokens <= MAX_TOKENS_MAX:
        raise LLMConfigError(f"max_tokens tiene que estar entre {MAX_TOKENS_MIN} y {MAX_TOKENS_MAX}.")
    if not TIMEOUT_MIN <= timeout_seconds <= TIMEOUT_MAX:
        raise LLMConfigError(f"El tiempo de espera tiene que estar entre {TIMEOUT_MIN} y {TIMEOUT_MAX} segundos.")

    if provider == PROVIDER_LOCAL:
        normalised = normalise_base_url(base_url or "")
    else:
        normalised = None
    return {
        "provider": provider,
        "base_url": normalised,
        "chat_model": chat_model.strip(),
        "analysis_model": analysis_model.strip(),
        # Left blank on purpose is a valid answer: it means "same as chat".
        "copilot_model": (copilot_model or "").strip(),
        "max_tokens": max_tokens,
        "timeout_seconds": timeout_seconds,
    }


# --------------------------------------------------------------- writing ---
def set_active(
    db: Session,
    *,
    provider: str,
    base_url: str | None,
    chat_model: str,
    analysis_model: str,
    api_key: str | None,
    max_tokens: int,
    timeout_seconds: int,
    label: str,
    copilot_model: str | None = None,
    actor_id=None,
) -> ResolvedConfig:
    """Insert a new active configuration and retire the previous one.

    Never updates in place. The old row stays, deactivated, so the record of
    which model was serving the app at any past moment survives the change.
    """
    fields = validate(
        provider=provider,
        base_url=base_url,
        chat_model=chat_model,
        analysis_model=analysis_model,
        copilot_model=copilot_model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )

    previous = active_row(db)
    # An empty key on an update means "leave it alone", not "clear it": the
    # UI never receives the stored key, so it cannot echo it back.
    effective_key = api_key if api_key is not None else (previous.api_key if previous else None)

    now = datetime.utcnow()
    for row in (
        db.query(LLMEndpointConfig).filter(LLMEndpointConfig.is_active == True).all()  # noqa: E712
    ):
        row.is_active = False
        row.deactivated_at = now
    # Retire the old row before inserting the new one. A partial unique index
    # allows only one active configuration, and SQLAlchemy's unit of work
    # emits INSERTs before UPDATEs, so without this flush the insert collides
    # with the row this call is in the middle of deactivating.
    db.flush()

    record = LLMEndpointConfig(
        provider=fields["provider"],
        base_url=fields["base_url"],
        chat_model=fields["chat_model"],
        analysis_model=fields["analysis_model"],
        copilot_model=fields["copilot_model"] or None,
        api_key=effective_key or None,
        max_tokens=fields["max_tokens"],
        timeout_seconds=fields["timeout_seconds"],
        label=(label or "").strip()[:120],
        is_active=True,
        created_by=actor_id,
        created_at=now,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    invalidate_cache()
    return _from_row(record)


def reset_to_environment(db: Session) -> ResolvedConfig:
    """Drop every override and go back to the deployment default."""
    now = datetime.utcnow()
    for row in (
        db.query(LLMEndpointConfig).filter(LLMEndpointConfig.is_active == True).all()  # noqa: E712
    ):
        row.is_active = False
        row.deactivated_at = now
    db.commit()
    invalidate_cache()
    return environment_config()
