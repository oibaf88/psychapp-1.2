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
    database_schema: str = ""

    # --- Auth -----------------------------------------------------------
    jwt_secret: str = "CHANGE_ME_DEV_ONLY_NOT_FOR_PRODUCTION"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12  # 12h, local/demo convenience

    # --- LLM (Anthropic / Claude) ---------------------------------------
    # Both agents run on the Anthropic API. No Claude model can run fully
    # offline: there are no downloadable weights. This app is "self-hosted"
    # in the sense that the server, database and UI run on infrastructure
    # you control, but Agent 1 (conversational) and Agent 2 (linguistic
    # analysis) call the Anthropic API over the network.
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""

    # Agent 1 — the conversational reply the patient reads.
    anthropic_chat_model: str = "claude-opus-5"
    anthropic_chat_effort: str = "medium"

    # Agent 2 — structured linguistic analysis feeding the risk engine.
    # Accuracy here drives alert levels, so it defaults to high effort.
    anthropic_analysis_model: str = "claude-opus-5"
    anthropic_analysis_effort: str = "high"

    # Caps thinking + response text together, so leave headroom.
    anthropic_max_tokens: int = 8192

    # --- Runtime LLM endpoint override ----------------------------------
    # Lets the two inference agents be pointed at a model you host yourself
    # (llama.cpp, Ollama, LM Studio, vLLM) from the Settings screen, without
    # redeploying, so a local model can be tried against the real app.
    #
    # The server fetches whatever URL is configured, so this is genuinely a
    # deployment decision and not only a UI one: patient text is sent to that
    # endpoint, and Agent 2's ability to spot a linguistic risk marker
    # becomes a property of the model behind it. Turn it off on any
    # deployment where the people using the app are not the people running
    # it. Every change is written to the audit log either way.
    llm_allow_runtime_override: bool = True

    # --- App / locale -----------------------------------------------------
    app_locale: str = "es-ES"
    app_env: str = "local"

    # --- Unfinished auth features (off by default) ----------------------
    # /auth/google-login currently trusts the client-supplied id_token as
    # the user's email instead of verifying it with Google, so enabling it
    # lets anyone obtain a session for any account. Keep it false until
    # real Google verification is implemented.
    allow_mock_google_login: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env not in ("local", "dev", "development")

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
