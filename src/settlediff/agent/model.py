"""Native Groq model factory for bounded PydanticAI investigations."""

from __future__ import annotations

from groq import AsyncGroq
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.providers.groq import GroqProvider

from settlediff.config import GroqConfig


def build_chat_model(config: GroqConfig) -> GroqModel:
    """Use provider-native tool responses and usage accounting without SDK retries."""
    client = AsyncGroq(
        base_url=config.base_url,
        api_key=config.api_key.get_secret_value(),
        timeout=config.timeout_seconds,
        max_retries=0,
    )
    return GroqModel(
        config.model_id,
        provider=GroqProvider(groq_client=client),
        settings={"temperature": 0},
    )
