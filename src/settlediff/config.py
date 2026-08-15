"""Environment-backed configuration with optional live integrations."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class HyperfusionConfig(BaseSettings):
    """Complete configuration required only when a live model is requested."""

    model_config = SettingsConfigDict(strict=True, extra="forbid", frozen=True)

    base_url: NonEmpty
    api_key: SecretStr
    model_id: NonEmpty
    timeout_seconds: float = Field(default=30, gt=0, le=300)

    @field_validator("api_key")
    @classmethod
    def require_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Hyperfusion API key must not be blank")
        return value


class ContextDevConfig(BaseSettings):
    """Complete configuration required for every live investigation."""

    model_config = SettingsConfigDict(strict=True, extra="forbid", frozen=True)

    base_url: NonEmpty = "https://api.context.dev/v1"
    api_key: SecretStr
    timeout_seconds: float = Field(default=10, gt=0, le=60)

    @field_validator("api_key")
    @classmethod
    def require_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Context.dev API key must not be blank")
        return value


class Settings(BaseSettings):
    """Application settings; live provider fields remain optional offline."""

    model_config = SettingsConfigDict(
        env_prefix="SETTLEDIFF_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    hyperfusion_base_url: str | None = None
    hyperfusion_api_key: SecretStr | None = None
    hyperfusion_model: str | None = None
    hyperfusion_timeout_seconds: float = Field(default=30, gt=0, le=300)
    database_path: str | None = None
    contextdev_base_url: str = "https://api.context.dev/v1"
    contextdev_api_key: SecretStr | None = None
    otlp_endpoint: str | None = None

    def require_contextdev(self) -> ContextDevConfig:
        """Return the Context.dev configuration required by a live investigation."""
        api_key = self.contextdev_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise ValueError("Context.dev configuration is required for live investigations")
        return ContextDevConfig(base_url=self.contextdev_base_url, api_key=api_key)

    def require_hyperfusion(self) -> HyperfusionConfig:
        values = (
            self.hyperfusion_base_url,
            self.hyperfusion_api_key,
            self.hyperfusion_model,
        )
        if any(value is None or (isinstance(value, str) and not value.strip()) for value in values):
            raise ValueError("Hyperfusion configuration is incomplete")
        assert self.hyperfusion_base_url is not None
        assert self.hyperfusion_api_key is not None
        assert self.hyperfusion_model is not None
        return HyperfusionConfig(
            base_url=self.hyperfusion_base_url,
            api_key=self.hyperfusion_api_key,
            model_id=self.hyperfusion_model,
            timeout_seconds=self.hyperfusion_timeout_seconds,
        )
