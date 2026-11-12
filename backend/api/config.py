"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Production-grade settings class with validation and environment loading."""

    DATABASE_URL: str = "sqlite:///./intentlock.db"
    AI_PROVIDER: str = "gemini"
    AI_API_KEY: str = ""
    AI_MODEL: str = "gemini-2.0-flash"
    QUOTE_TTL_MINUTES: int = 15
    APPROVAL_TTL_MINUTES: int = 15
    DEMO_ADMIN_TOKEN: str = "demo-admin-secret-token"
    DEMO_ALLOWED_ENVS: list[str] = ["dev", "development", "staging", "demo"]
    AI_REWORD_ENABLED: bool = False
    SUPABASE_JWT_SECRET: str = "demo-supabase-jwt-secret"
    ENV: str = "development"
    CORS_ORIGINS: list[str] | str = ["*"]
    ALLOWED_CURRENCIES: list[str] = ["USD", "EUR", "GBP"]
    ALLOWED_CATEGORIES: list[str] = ["laptop", "laptops", "electronics"]
    DEFAULT_SHIPPING_MINOR: int = 0
    DEFAULT_TAX_MINOR: int = 0
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PAYPAL_BASE_URL: str = "https://api-m.sandbox.paypal.com"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: str | list[str]) -> list[str]:
        """Parse CORS origins if provided as comma-separated string."""
        if isinstance(v, str):
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    pass
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
