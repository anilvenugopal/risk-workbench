"""Application settings — resolved from environment variables via pydantic-settings.

All secrets (passwords, keys) come from the environment or a .env file.
No secrets are stored in code or VCS. See infra/.env.example.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_env: Literal["development", "production"] = "development"
    app_title: str = "Risk Analysis Workbench"
    app_version: str = "0.1.0"

    # ── Auth ──────────────────────────────────────────────────────────────────
    # AUTH_MODE controls the login page and which auth paths are active:
    #   password — password form only (non-Entra users, e.g. contractors)
    #   oidc     — "Sign in with Microsoft" only (PremiumIQ Entra accounts)
    #   both     — both options shown; recommended for development
    # ENTRA_* vars are required when auth_mode is "oidc" or "both".
    auth_mode: Literal["password", "oidc", "both"] = "both"

    # Session signing key — generate with: python -c "import secrets; print(secrets.token_hex(32))"
    session_secret_key: str = Field(
        ...,
        description="Secret key for signing session cookies. Required.",
    )

    # ── OIDC (required when auth_mode is 'oidc' or 'both') ───────────────────
    entra_client_id: str = ""
    entra_tenant_id: str = ""
    entra_client_secret: str = ""
    entra_redirect_uri: str = "http://localhost:8000/auth/callback"

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://127.0.0.1:6379/0"

    # ── Paths ─────────────────────────────────────────────────────────────────
    submission_outputs_base: str = "/workspace/data/outputs"

    @computed_field
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @computed_field
    @property
    def password_auth_enabled(self) -> bool:
        return self.auth_mode in ("password", "both")

    @computed_field
    @property
    def oidc_auth_enabled(self) -> bool:
        return self.auth_mode in ("oidc", "both")


settings = Settings()
