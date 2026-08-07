"""
PsychApp backend configuration.

All values are read from environment variables (see ../.env.example at the
project root). Nothing here should ever contain a real secret.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database -----------------------------------------------------
    database_url: str = "postgresql://psychapp:psychapp@db:5432/psychapp"

    # --- Auth -----------------------------------------------------------
    jwt_secret: str = "CHANGE_ME_DEV_ONLY_NOT_FOR_PRODUCTION"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12  # 12h, local/demo convenience

    # --- LLM (Anthropic / Claude) ---------------------------------------
    # No Claude model can run fully offline: there are no downloadable
    # weights. This app is "locally executable" in the sense that the
    # server, database and UI all run on your machine, but the
    # conversational (Agent 1) and linguistic-analysis (Agent 2) features
    # call the Anthropic API over the network and require a valid key.
    llm_provider: str = "anthropic"  # swappable, see app/services/llm/
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5-20250929"
    anthropic_max_tokens: int = 1024

    # --- App / locale -----------------------------------------------------
    app_locale: str = "es-ES"
    app_env: str = "local"

    # --- Notifications (all optional; app works with none configured) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "psychapp@localhost"

    # --- CORS -------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- Seed data ----------------------------------------------------
    seed_demo_data: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
