"""LLM provider factory.

PsychApp's agents run on whichever provider is configured. Anthropic calls
also receive a metadata-only usage recorder so every billable request can be
reconciled independently of the clinical tables.
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
            max_tokens=config.max_tokens,
            timeout_seconds=float(config.timeout_seconds),
        )

    # Imported lazily to keep the provider module usable in isolated unit
    # tests without opening a database session merely by importing it.
    from app.services.llm_usage import record_usage_safely

    return AnthropicProvider(
        chat_model=config.chat_model,
        analysis_model=config.analysis_model,
        copilot_model=config.copilot_model,
        # Only a runtime override pins one budget across both roles. Passing
        # the environment's shared value here would shadow the per-role
        # settings.
        max_tokens=config.explicit_max_tokens,
        usage_recorder=record_usage_safely,
    )


def get_llm_provider(db=None) -> LLMProvider:
    """Return the provider currently in force."""
    from app.services import llm_config

    return build_provider(llm_config.resolve(db))
