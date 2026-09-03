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
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address
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
# Connection establishment stays fail-fast (see CONNECT_TIMEOUT_SECONDS on
# the local provider). This ceiling is the inference wait once the TCP
# handshake has succeeded — a local model loading into VRAM can take minutes.
TIMEOUT_MIN, TIMEOUT_MAX = 5, 5000

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


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
    # What was actually configured, before the "empty means chat" fallback.
    # The UI needs this: prefilling the edit form with the resolved value
    # turns "follows chat" into "pinned to whatever chat was", so changing
    # the chat model afterwards silently leaves the copilot behind.
    copilot_model_explicit: str = ""
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

    @property
    def explicit_max_tokens(self) -> int | None:
        """The token budget a runtime override actually asked for.

        None for the environment default. `max_tokens` is always populated —
        the environment fills it from ANTHROPIC_MAX_TOKENS — so passing it
        into a provider unconditionally made that shared value shadow the
        per-role settings on every ordinary call, and
        ANTHROPIC_MAX_TOKENS_CHAT / _ANALYSIS did nothing at all. A stored
        override carries one budget for both roles by design; the
        environment does not, and must not pretend to.
        """
        return self.max_tokens if self.source == "runtime" else None

    @property
    def copilot_model_is_inherited(self) -> bool:
        """True when the copilot follows chat rather than being pinned."""
        return not self.copilot_model_explicit.strip()

    def public_dict(self) -> dict:
        """Everything the UI may see. Never the key."""
        settings = get_settings()
        anthropic_key_present = bool(settings.anthropic_api_key)
        if self.provider == PROVIDER_ANTHROPIC:
            has_key = anthropic_key_present
        else:
            has_key = bool(self.api_key)
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
            "copilot_model_explicit": self.copilot_model_explicit,
            "copilot_model_is_inherited": self.copilot_model_is_inherited,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "source": self.source,
            "config_id": self.config_id,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "has_api_key": has_key,
            "uses_server_api_key": self.provider == PROVIDER_ANTHROPIC,
        }


class LLMConfigError(ValueError):
    """The submitted configuration cannot be used."""


def backend_runtime() -> str:
    """Where this FastAPI process is actually running.

    Render sets RENDER / RENDER_SERVICE_ID. Production APP_ENV is treated
    the same: the process is not on the operator's LAN, so a 192.168/10/127
    address is unroutable from here.
    """
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        return "cloud"
    if get_settings().is_production:
        return "cloud"
    return "local"


def backend_runtime_label() -> str:
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        region = os.environ.get("RENDER_REGION") or "frankfurt"
        return f"Render ({region})"
    if get_settings().is_production:
        return "servidor en la nube"
    return "este equipo (proceso local de FastAPI)"


def _hostname_is_private(hostname: str) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return True
    if host in LOOPBACK_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)


def endpoint_reachability(url: str | None) -> dict:
    """Can THIS FastAPI process open a TCP connection to that URI?

    A 10 s connect timeout is the wrong answer when the address is a
    private LAN IP and we are in Frankfurt: there is no route, so we
    refuse immediately rather than hanging.
    """
    runtime = backend_runtime()
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    private = _hostname_is_private(host)
    if runtime == "local":
        return {
            "ok": True,
            "runtime": runtime,
            "private_target": private,
            "reason": None,
        }
    if not url:
        return {
            "ok": False,
            "runtime": runtime,
            "private_target": True,
            "reason": "Falta la URL del modelo.",
        }
    if private:
        return {
            "ok": False,
            "runtime": runtime,
            "private_target": True,
            "reason": (
                f"Este backend corre en {backend_runtime_label()} y no tiene ruta a "
                f"{host}. Una IP de LAN (127.0.0.1, 192.168.x, 10.x) no es un endpoint "
                "alcanzable desde Frankfurt. Usa Claude (la clave ANTHROPIC_API_KEY ya "
                "está en el servidor) o un túnel HTTPS público autenticado "
                "(Cloudflare Tunnel, ngrok) que apunte a tu LM Studio."
            ),
        }
    if parsed.scheme != "https":
        return {
            "ok": False,
            "runtime": runtime,
            "private_target": False,
            "reason": (
                "Desde un despliegue en la nube el endpoint del modelo tiene que ser "
                "HTTPS público. HTTP en claro enviaría texto clínico sin cifrar."
            ),
        }
    return {
        "ok": True,
        "runtime": runtime,
        "private_target": False,
        "reason": None,
    }


def _is_unreachable_local(config: "ResolvedConfig") -> bool:
    if not config.is_local:
        return False
    return not endpoint_reachability(config.base_url)["ok"]


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
        copilot_model_explicit=settings.anthropic_copilot_model.strip(),
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
        copilot_model_explicit=row.copilot_model or "",
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


def stored_override(db: Session | None) -> ResolvedConfig | None:
    """The row the operator last saved, even if this host cannot reach it."""
    if db is None:
        return None
    try:
        row = active_row(db)
    except Exception:  # noqa: BLE001
        return None
    return _from_row(row) if row else None


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
        stored = _from_row(row) if row else None
        if stored is None:
            config = environment_config()
        elif _is_unreachable_local(stored):
            # Keep the stored row (the Settings screen still shows it) but do
            # not send inference there: hanging for minutes on an unroutable
            # LAN address is how production looked broken.
            logger.warning(
                "Ignoring unreachable local LLM endpoint %s from %s; using the environment default",
                stored.base_url,
                backend_runtime_label(),
            )
            config = environment_config()
        else:
            config = stored
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
    # LM Studio's "copy server URL" sometimes yields /api/v1/chat instead of /v1.
    if url.endswith("/api/v1/chat"):
        url = url[: -len("/api/v1/chat")] + "/v1"
    elif url.endswith("/api/v1"):
        url = url[: -len("/api/v1")] + "/v1"
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
        reach = endpoint_reachability(normalised)
        if not reach["ok"]:
            raise LLMConfigError(reach["reason"])
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
    # Anthropic always authenticates with ANTHROPIC_API_KEY from the server
    # environment (Render secret). A key typed in the browser is ignored so
    # it can never shadow or leak the deployment credential.
    if fields["provider"] == PROVIDER_ANTHROPIC:
        effective_key = None
    else:
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
