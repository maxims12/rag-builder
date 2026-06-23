"""Application settings loaded from environment via pydantic-settings.

All secrets (JWT secret, admin password, provider API keys) are read from the
environment / .env file here. Never hardcode secrets elsewhere in the codebase.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend runtime configuration.

    Field names map (case-insensitively) to the environment variables defined in
    .env.example. Extra env vars (frontend-only, compose-only) are ignored.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Auth / JWT ────────────────────────────────────────────────────
    jwt_secret: str = "change_me_to_a_long_random_string"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # ── Seed admin (created on first boot) ────────────────────────────
    admin_email: str = "admin@example.com"
    admin_password: str = "change_this_password"

    # ── Backend core ──────────────────────────────────────────────────
    database_url: str = "sqlite:///./data/rag_builder.db"
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    # ── Vector store (used in later phases) ───────────────────────────
    qdrant_url: str = "http://localhost:6333"

    # ── Provider API keys (only used when a provider is selected) ─────
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    cohere_api_key: str | None = None
    groq_api_key: str | None = None
    voyage_api_key: str | None = None

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse the comma-separated CORS origins string into a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (single load per process)."""
    return Settings()


settings = get_settings()
