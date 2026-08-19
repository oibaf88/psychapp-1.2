"""LLM provider factory.

PsychApp's two inference agents — Agent 1 (conversational), Agent 2
(linguistic analyst) and Agent 4 (psychosocial extractor) — run on whichever
provider is configured. Two are supported:

  * ``anthropic`` — Claude over the Anthropic API. The default, and what the
    clinical prompts were tuned against.
  * ``openai_compatible`` — a model you host yourself, reached over the
    OpenAI chat-completions API that llama.cpp, Ollama, LM Studio, vLLM and
    LocalAI all expose.

The choice comes from ``app/services/llm_config.py``: the environment by
default, overridden at runtime by the active row in ``llm_endpoint_configs``
when the deployment allows it. Callers do not pass it — they ask for a
provider and get whichever one is in force, with the metadata to prove which
one answered.
"""
from app.services.llm.anthropic_provider import AnthropicProvider, RefusalError
from app.services.llm.base import LLMProvider, ProviderMetadata, StructuredAnalysisError, StructuredAnalysisResult
from app.services.llm.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "LLMProvider",
    "ProviderMetadata",
    "StructuredAnalysisError",
    "StructuredAnalysisResult",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "RefusalError",
    "get_llm_provider",
    "build_provider",
]


def build_provider(config) -> LLMProvider:
    """Construct the provider one resolved configuration describes."""
    from app.services import llm_config

    if config.provider == llm_config.PROVIDER_LOCAL:
        return OpenAICompatibleProvider(
            base_url=config.base_url,
            chat_model=config.chat_model,
            analysis_model=config.analysis_model,
            api_key=config.api_key,
            max_tokens=config.max_tokens,
            timeout_seconds=float(config.timeout_seconds),
        )
    return AnthropicProvider(
        chat_model=config.chat_model,
        analysis_model=config.analysis_model,
        max_tokens=config.max_tokens,
    )


def get_llm_provider(db=None) -> LLMProvider:
    """The provider currently in force.

    ``db`` is optional so existing call sites keep working; passing it lets
    the resolver see a configuration change made on another worker without
    waiting for the cache to expire.
    """
    from app.services import llm_config

    return build_provider(llm_config.resolve(db))
