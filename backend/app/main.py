import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.routers import (
    assignments,
    audit,
    auth,
    chat,
    checkins,
    consents,
    diary,
    facts,
    notifications,
    professional,
    safety,
    timeline,
    metrics,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("psychapp")

settings = get_settings()

app = FastAPI(
    title="PsychApp API",
    description=(
        "Self-regulation & self-awareness companion (Level A/B MVP, see README). "
        "Not a medical device. Conversational and linguistic-analysis features are "
        "powered by Claude via the Anthropic API and require ANTHROPIC_API_KEY."
    ),
    version="0.1.0",
)

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
# Local/dev: also accept any private LAN origin (phone on same Wi‑Fi via
# http://192.168.x.x:5173). Production should set APP_ENV and explicit CORS_ORIGINS.
_cors_kwargs: dict = {
    "allow_origins": origins or ["http://localhost:5173"],
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if settings.app_env in ("local", "dev", "development"):
    # LAN + Cloudflare quick tunnels (https://*.trycloudflare.com) + common tunnel hosts
    _cors_kwargs["allow_origin_regex"] = (
        r"https?://("
        r"localhost|127\.0\.0\.1|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|"
        r"[\w-]+\.trycloudflare\.com|"
        r"[\w-]+\.ngrok(-free)?\.app|"
        r"[\w-]+\.ngrok\.io"
        r")(:\d+)?$"
    )
app.add_middleware(CORSMiddleware, **_cors_kwargs)


def _wait_for_db(max_attempts: int = 30, delay_seconds: float = 2.0) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection established.")
            return
        except OperationalError:
            logger.info("Database not ready yet (attempt %s/%s), retrying...", attempt, max_attempts)
            time.sleep(delay_seconds)
    raise RuntimeError("Could not connect to the database after multiple attempts.")


@app.on_event("startup")
def on_startup():
    _wait_for_db()
    Base.metadata.create_all(bind=engine)
    logger.info("Database schema ensured (create_all).")

    if settings.seed_demo_data:
        from app.seed import seed_demo_data

        db = SessionLocal()
        try:
            seed_demo_data(db)
        finally:
            db.close()

    if not settings.anthropic_api_key:
        logger.warning(
            "ANTHROPIC_API_KEY is not set. The app will run, but /api/v1/chat and the "
            "linguistic-analysis (Agent 2) features will fail until you set it in .env."
        )


@app.get("/api/v1/health")
def health():
    return {
        "status": "ok",
        "llm_configured": bool(settings.anthropic_api_key),
        "llm_provider": settings.llm_provider,
        "chat_model": settings.anthropic_chat_model,
        "analysis_model": settings.anthropic_analysis_model,
    }


app.include_router(auth.router)
app.include_router(consents.router)
app.include_router(checkins.router)
app.include_router(diary.router)
app.include_router(timeline.router)
app.include_router(chat.router)
app.include_router(safety.router)
app.include_router(facts.router)
app.include_router(assignments.router)
app.include_router(professional.router)
app.include_router(notifications.router)
app.include_router(audit.router)
app.include_router(metrics.router)
