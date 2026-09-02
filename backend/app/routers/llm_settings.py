"""
Runtime LLM endpoint configuration, from the Settings screen.

PsychApp ships pointed at Claude. This lets whoever is running it point the
inference agents at a model they host themselves and see how the app behaves
on it — the reason the endpoint is editable at all.

Who may change it
-----------------
Reading is open to any authenticated account: knowing which model is
answering is part of understanding what the app just told you, and the
payload carries no secret. Writing takes two separate permissions:

* ``LLM_ALLOW_RUNTIME_OVERRIDE`` gates the whole feature at deployment
  level, and is **off** unless the deployment turns it on. With it off the
  endpoints report the environment configuration and refuse writes.
* ``admin_clinical`` gates the account. Redirecting the agents sends
  patient text to whatever server is named, so it is an operator action,
  not something a patient or a therapist should be able to do by opening a
  settings screen. This deliberately makes the single-operator case a
  little less convenient — that operator has to sign in as the admin
  account — in exchange for the shared case being safe by default.

Every change is written to the audit log with who made it and what the
endpoint became, and the previous configuration is retained rather than
overwritten.

What is deliberately not exposed
--------------------------------
The stored API key is never returned, not even masked beyond a boolean.
Submitting ``null`` leaves the existing key untouched; submitting ``""``
clears it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User
from app.schemas import (
    LLMEndpointConfigIn,
    LLMEndpointStatusOut,
    LLMEndpointTestIn,
    LLMEndpointTestOut,
)
from app.security import get_current_user, require_admin
from app.services import audit, llm_config
from app.services.llm import build_provider
from app.services.llm.base import StructuredAnalysisError
from app.services.llm.openai_compatible import PROVIDER_NAME as LOCAL_PROVIDER

logger = logging.getLogger("psychapp.llm_settings")

router = APIRouter(prefix="/api/v1/settings/llm", tags=["settings"])

WARNING_LOCAL = (
    "Con un modelo propio, el texto clínico del paciente se envía al servidor que indiques y no a "
    "la API de Anthropic. La calidad de la detección de señales lingüísticas del Agente 2 pasa a "
    "depender de ese modelo. El motor de riesgo es determinista y no cambia: ningún modelo decide "
    "un nivel de alerta."
)
WARNING_DISABLED = (
    "El cambio de endpoint está desactivado en este despliegue (LLM_ALLOW_RUNTIME_OVERRIDE=false). "
    "Se usa la configuración del entorno."
)
WARNING_NOT_ADMIN = (
    "Solo una cuenta de administración clínica puede cambiar el endpoint del modelo: apuntarlo a otro "
    "servidor envía el texto de los pacientes a ese servidor. Puedes ver qué modelo está atendiendo, "
    "pero no modificarlo."
)

ADMIN_ROLE = "admin_clinical"


def _status(db: Session, user: User) -> LLMEndpointStatusOut:
    settings = get_settings()
    active = llm_config.resolve(db)
    environment = llm_config.environment_config()
    allowed = settings.llm_allow_runtime_override
    is_admin = user.role == ADMIN_ROLE

    if active.is_local:
        notice = WARNING_LOCAL
    elif not allowed:
        notice = WARNING_DISABLED
    elif not is_admin:
        notice = WARNING_NOT_ADMIN
    else:
        notice = None

    return LLMEndpointStatusOut(
        active=active.public_dict(),
        environment_default=environment.public_dict(),
        override_allowed=allowed,
        can_edit=allowed and is_admin,
        is_local=active.is_local,
        notice=notice,
    )


@router.get("", response_model=LLMEndpointStatusOut)
def read_llm_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Which model is serving the app right now."""
    return _status(db, user)


@router.put("", response_model=LLMEndpointStatusOut)
def update_llm_settings(
    payload: LLMEndpointConfigIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Point the inference agents at a different endpoint."""
    settings = get_settings()
    if not settings.llm_allow_runtime_override:
        raise HTTPException(status_code=403, detail=WARNING_DISABLED)

    try:
        config = llm_config.set_active(
            db,
            provider=payload.provider,
            base_url=payload.base_url,
            chat_model=payload.chat_model,
            analysis_model=payload.analysis_model,
            copilot_model=payload.copilot_model,
            api_key=payload.api_key,
            max_tokens=payload.max_tokens,
            timeout_seconds=payload.timeout_seconds,
            label=payload.label or "",
            actor_id=user.id,
        )
    except llm_config.LLMConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    audit.log(
        db,
        actor_id=user.id,
        actor_role=user.role,
        action="llm_endpoint_changed",
        entity_type="llm_endpoint_config",
        entity_id=config.config_id,
        # The endpoint is recorded; the key never is.
        extra={
            "provider": config.provider,
            "base_url": config.base_url,
            "chat_model": config.chat_model,
            "analysis_model": config.analysis_model,
            "copilot_model": config.copilot_model,
        },
    )
    return _status(db, user)


@router.delete("", response_model=LLMEndpointStatusOut)
def reset_llm_settings(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Go back to the model configured in the deployment environment."""
    settings = get_settings()
    if not settings.llm_allow_runtime_override:
        raise HTTPException(status_code=403, detail=WARNING_DISABLED)
    llm_config.reset_to_environment(db)
    audit.log(
        db,
        actor_id=user.id,
        actor_role=user.role,
        action="llm_endpoint_reset",
        entity_type="llm_endpoint_config",
    )
    return _status(db, user)


@router.post("/test", response_model=LLMEndpointTestOut)
def test_llm_endpoint(
    payload: LLMEndpointTestIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Try a candidate endpoint before committing to it.

    Sends one trivial prompt and reports what came back. Nothing is saved and
    no patient text is involved, so this is safe to run against a server that
    turns out to be the wrong one.
    """
    settings = get_settings()
    if not settings.llm_allow_runtime_override:
        raise HTTPException(status_code=403, detail=WARNING_DISABLED)

    try:
        fields = llm_config.validate(
            provider=payload.provider,
            base_url=payload.base_url,
            chat_model=payload.chat_model,
            analysis_model=payload.analysis_model or payload.chat_model,
            copilot_model=payload.copilot_model,
            max_tokens=512,
            timeout_seconds=payload.timeout_seconds,
        )
    except llm_config.LLMConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    candidate = llm_config.ResolvedConfig(
        provider=fields["provider"],
        chat_model=fields["chat_model"],
        analysis_model=fields["analysis_model"],
        copilot_model=fields["copilot_model"],
        base_url=fields["base_url"],
        api_key=payload.api_key or "",
        max_tokens=512,
        timeout_seconds=payload.timeout_seconds,
    )
    provider = build_provider(candidate)

    audit.log(
        db,
        actor_id=user.id,
        actor_role=user.role,
        action="llm_endpoint_tested",
        entity_type="llm_endpoint_config",
        extra={"provider": candidate.provider, "base_url": candidate.base_url},
    )

    try:
        reply = provider.chat(
            "Responde solamente con la palabra OK.",
            [{"role": "user", "content": "Responde OK."}],
            max_tokens=16,
        ).text
    except StructuredAnalysisError as exc:
        return LLMEndpointTestOut(
            ok=False,
            detail=_test_failure_detail(exc.safe_kind, exc.error_code),
            error_code=exc.error_code,
            base_url=candidate.base_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM endpoint test failed: %s", type(exc).__name__)
        return LLMEndpointTestOut(
            ok=False,
            detail=f"No se pudo contactar con el endpoint ({type(exc).__name__}).",
            base_url=candidate.base_url,
        )

    sample = (reply or "").strip()
    return LLMEndpointTestOut(
        ok=bool(sample),
        detail=(
            f"El servidor respondió: «{sample[:120]}»"
            if sample
            else "El servidor respondió, pero con un mensaje vacío. Revisa el nombre del modelo."
        ),
        sample=sample[:400] or None,
        base_url=candidate.base_url,
    )


def _test_failure_detail(safe_kind: str, error_code: str | None) -> str:
    if error_code == "local_endpoint_unreachable":
        return (
            "No se llegó al servidor. Comprueba que está arrancado y que la URL es accesible desde "
            "el backend (dentro de Docker, 'localhost' es el contenedor: usa host.docker.internal "
            "o la IP de tu equipo)."
        )
    if error_code == "local_endpoint_timeout":
        return "El servidor no respondió a tiempo. Sube el tiempo de espera o usa un modelo más pequeño."
    if error_code == "http_404":
        return "El servidor respondió 404. Suele faltar el sufijo /v1 en la URL."
    if error_code in ("http_401", "http_403"):
        return "El servidor pide autenticación. Rellena la API key."
    if safe_kind == "configuration_error":
        return "Configuración rechazada por el servidor. Revisa la URL, la clave y el nombre del modelo."
    return f"El endpoint devolvió un error ({error_code or safe_kind})."
