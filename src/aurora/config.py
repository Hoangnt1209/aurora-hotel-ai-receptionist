"""Application settings loaded from environment variables or .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for Aurora backend and database connections."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = (
        "postgresql+asyncpg://aurora:aurora_dev_password@localhost:5432/aurora_hotel"
    )
    database_url_sync: str = (
        "postgresql+psycopg://aurora:aurora_dev_password@localhost:5432/aurora_hotel"
    )
    jwt_secret_key: str = "aurora-dev-jwt-secret-change-in-production-2026"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    verification_token_ttl_seconds: int = 1800
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    vieneu_mode: str = "auto"  # auto | local_sdk | remote_api | fast_wav
    vieneu_api_url: str | None = None
    vieneu_default_voice: str = "Mai Anh"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
