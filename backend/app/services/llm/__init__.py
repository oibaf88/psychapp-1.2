from app.services.llm.base import LLMProvider
from app.services.llm.anthropic_provider import AnthropicProvider
from app.config import get_settings

_settings = get_settings()


def get_llm_provider() -> LLMProvider:
    """
    Provider factory. Doc 1 explicitly requires a "modular architecture
    allowing model swap without redoing the product", and doc 7 shows the
    team also explored fine-tuned local models (Ollama). This factory is
    the seam for that: implement LLMProvider for e.g. an Ollama-backed
    provider and select it via LLM_PROVIDER=ollama, without touching any
    router or the risk engine.
    """
    if _settings.llm_provider == "anthropic":
        return AnthropicProvider()
    raise ValueError(
        f"Unknown LLM_PROVIDER '{_settings.llm_provider}'. "
        "Only 'anthropic' is implemented in this build; see app/services/llm/base.py "
        "to add another provider (e.g. a local Ollama model)."
    )
