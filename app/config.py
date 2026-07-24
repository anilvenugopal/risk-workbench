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

    # Read-only shared drive the broker files are browsed from (FR-008/FR-009/R11).
    # The app never writes/moves/deletes under this root — browsing is a live
    # directory listing. Empty in dev without a mounted drive.
    shared_drive_root: str = ""

    # ── Background queue / poller (Iteration 2, Article 10 / Article 11) ────────
    # rwb_job heartbeat cadence + the staleness window the poller's reconciler uses
    # to reclaim a dead worker's `running` row back to `pending`.
    rwb_heartbeat_interval_secs: int = 30
    rwb_heartbeat_stale_secs: int = 120

    # Poller pass cadence (FR-027 / SC-001). One single-status-check per
    # non-terminal irp_job per pass; never poll_*_to_completion.
    poll_interval_secs: int = 15

    # Submit-side retry ceiling for the submission_retry batch (FR-029). There is
    # deliberately NO fixed default — it is a deployment decision; None means "not
    # configured", and the batch parks SUBMISSION FAILED rows until it is set.
    irp_submission_max_retries: int | None = None

    # ── Risk Modeler / IRP gateway (Article 11) ─────────────────────────────────
    # irp-integration's IRPClient() reads ALL of its own config straight from the
    # environment (the gateway constructs it with no args), so those are NOT pydantic
    # settings here — they must be exported into the process env (see
    # infra/.env.example). Always required: RISK_MODELER_BASE_URL,
    # RISK_MODELER_RESOURCE_GROUP_ID. Auth is auto-selected: RISK_MODELER_TENANT_NAME
    # + _USERNAME + _PASSWORD for bearer login, or RISK_MODELER_API_KEY for the
    # api-key strategy (the key wins if both are set). S3 import staging needs no
    # ambient AWS creds — the wheel gets short-lived upload credentials from Risk
    # Modeler per transfer.
    #
    # The one genuinely app-owned IRP setting is the EDM-import target DB-server
    # *name* (RDM import + EDM delete resolve their server inside the wheel); the
    # default matches irp-integration's own default.
    irp_edm_import_server: str = "databridge-1"

    # The BASE_URL alone is ALSO mirrored here (read-only): the web layer builds
    # deep links into Risk Modeler's own UI from it (e.g. the EDM treaties
    # screen) — never an API call. Empty (unset) simply hides those links.
    risk_modeler_base_url: str = ""

    # ── Notifications (Iteration 2, R10) ────────────────────────────────────────
    # Comma-separated channels to deliver completion/failure notices on
    # (any of: teams, email, desktop). Enabling a channel is a config edit.
    notify_channels: str = ""
    teams_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 25
    smtp_from: str = ""
    notify_email_to: str = ""

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
