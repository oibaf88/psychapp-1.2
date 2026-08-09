"""LLM provider factory.

Both PsychApp agents run on the Anthropic API — Agent 1 (conversational)
and Agent 2 (linguistic analyst) — each with its own configurable model.
See app/services/llm/anthropic_provider.py.
"""
from app.config import get_settings
from app.services.llm.anthropic_provider import AnthropicProvider, RefusalError
from app.services.llm.base import LLMProvider

__all__ = ["LLMProvider", "AnthropicProvider", "RefusalError", "get_llm_provider"]

_settings = get_settings()


def get_llm_provider() -> LLMProvider:
    if _settings.llm_provider != "anthropic":
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{_settings.llm_provider}'. PsychApp runs both "
            "agents on the Anthropic API; the only accepted value is 'anthropic'. "
            "Choose the per-agent models with ANTHROPIC_CHAT_MODEL and "
            "ANTHROPIC_ANALYSIS_MODEL instead."
        )
    return AnthropicProvider()
