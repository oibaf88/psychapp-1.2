"""LLM provider factory.

PsychApp's agents — Agent 1 (conversational), Agent 2 (linguistic analyst),
Agent 3 (clinical copilot) and Agent 4 (psychosocial extractor) — run on
whichever provider is configured. Two are supported:

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
from app.services.llm.anthropic_provider import AnthropicProvider
from app.services.llm.base import (
    ChatResult,
    LLMProvider,
    ProviderMetadata,
    StructuredAnalysisError,
    StructuredAnalysisResult,
)
from app.services.llm.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "ChatResult",
    "LLMProvider",
    "ProviderMetadata",
    "StructuredAnalysisError",
    "StructuredAnalysisResult",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
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
            copilot_model=config.copilot_model,
            api_key=config.api_key,
            # A local endpoint has one budget for everything it serves, so
            # the resolved value applies whether it came from a stored row
            # or the environment.
            max_tokens=config.max_tokens,
            timeout_seconds=float(config.timeout_seconds),
        )
    return AnthropicProvider(
        chat_model=config.chat_model,
        analysis_model=config.analysis_model,
        copilot_model=config.copilot_model,
        # Only a runtime override pins one budget across both roles. Passing
        # the environment's shared value here would shadow the per-role
        # settings, which is how ANTHROPIC_MAX_TOKENS_CHAT / _ANALYSIS came
        # to be documented while doing nothing.
        max_tokens=config.explicit_max_tokens,
    )


def get_llm_provider(db=None) -> LLMProvider:
    """The provider currently in force.

    ``db`` is optional so existing call sites keep working; passing it lets
    the resolver see a configuration change made on another worker without
    waiting for the cache to expire.
    """
    from app.services import llm_config

    return build_provider(llm_config.resolve(db))
