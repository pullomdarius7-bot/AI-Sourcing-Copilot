from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    environment: str = "production"  # development | test | production
    database_url: str
    jwt_secret: str = Field(min_length=32)
    jwt_issuer: str = "ai-sourcing-copilot"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    refresh_reuse_grace_seconds: int = 10
    mfa_token_minutes: int = 5
    mfa_encryption_key: str
    mfa_issuer: str = "AI Sourcing Copilot"

    email_backend: str = "smtp"  # smtp | console | memory
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False
    email_from: str = "AI Sourcing Copilot <no-reply@sourcing.local>"
    app_base_url: str = "http://localhost:3000"

    require_email_verification: bool = True
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    verify_token_hours: int = 48
    reset_token_minutes: int = 60
    invite_token_days: int = 7

    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 30
    cors_origins: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
