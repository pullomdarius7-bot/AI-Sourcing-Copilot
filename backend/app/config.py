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

    # --- Documents & storage ---
    max_upload_mb: int = 20
    max_text_chars: int = 200_000
    storage_backend: str = "auto"  # auto (MinIO, falling back to local) | minio | local
    storage_local_dir: str = "/data/uploads"
    minio_endpoint: str = ""  # host:port; empty disables MinIO
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "documents"
    minio_secure: bool = False
    minio_retry_after_seconds: int = 30

    # --- Virus scanning ---
    scan_backend: str = "both"  # both (built-in + ClamAV) | clamav | builtin
    clamav_host: str = "clamav"
    clamav_port: int = 3310
    clamav_timeout_seconds: int = 60

    # --- Job workers ---
    worker_concurrency: int = 2
    job_poll_interval_seconds: float = 1.0
    job_timeout_seconds: int = 300
    job_visibility_seconds: int = 600
    job_max_attempts: int = 5
    job_backoff_base_seconds: float = 5.0
    job_backoff_max_seconds: float = 900.0
    job_retention_days: int = 7

    # --- AI layer ---
    ai_provider_order: str = "local,anthropic,openai"
    ai_timeout_seconds: float = 120.0
    ai_max_input_chars: int = 60_000
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    local_llm_url: str = ""  # Ollama base URL, e.g. http://ollama:11434
    local_llm_model: str = "llama3.1:8b"

    # --- Requirement extraction & review ---
    require_separate_approver: bool = True  # the approver may not have created or reviewed the run
    bulk_verify_min_confidence: float = 0.85
    extraction_chunk_chars: int = 40_000
    extraction_chunk_overlap: int = 600
    extraction_max_chunks: int = 8

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
