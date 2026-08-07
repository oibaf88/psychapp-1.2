from app.services.llm.base import LLMProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.anthropic_provider import AnthropicProvider
from app.config import get_settings

_settings = get_settings()


def get_llm_provider() -> LLMProvider:
    if _settings.llm_provider == "anthropic":
        return AnthropicProvider()
    elif _settings.llm_provider == "openai":
        return OpenAIProvider()
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{_settings.llm_provider}'. "
            "Set to 'anthropic' or 'openai'."
        )