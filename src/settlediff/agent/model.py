"""PydanticAI Chat Completions model factory for Hyperfusion and Groq."""

from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from settlediff.config import ChatCompletionsConfig

CHAT_COMPLETIONS_PROFILE = OpenAIModelProfile(
    supports_tools=True,
    supports_json_schema_output=False,
    supports_json_object_output=False,
    default_structured_output_mode="prompted",
    openai_supports_strict_tool_definition=False,
)


def build_chat_model(config: ChatCompletionsConfig) -> OpenAIChatModel:
    """Build an explicit OpenAI-compatible Chat model with SDK retries disabled."""
    client = AsyncOpenAI(
        base_url=config.base_url,
        api_key=config.api_key.get_secret_value(),
        timeout=config.timeout_seconds,
        max_retries=0,
    )
    provider = OpenAIProvider(openai_client=client)
    return OpenAIChatModel(
        config.model_id,
        provider=provider,
        profile=CHAT_COMPLETIONS_PROFILE,
    )


# Preserve the existing factory import for Hyperfusion integrations.
build_hyperfusion_model = build_chat_model
