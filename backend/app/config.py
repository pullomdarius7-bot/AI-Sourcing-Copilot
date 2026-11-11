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

    # --- Free trial: ends after trial_days or trial_credits analyses, whichever comes first ---
    trial_days: int = 7
    trial_credits: int = 10

    # --- Billing ---
    # false = credits and subscriptions are not checked (self-hosted installs that do not sell access)
    billing_enforced: bool = True
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Optional price ids. Left empty, each price is found by its lookup key, which equals the plan key.
    stripe_price_solo_buyer_monthly: str = ""
    stripe_price_enterprise_buyer_monthly: str = ""
    stripe_price_supplier_monthly: str = ""
    plan_credits_solo: int = 25
    plan_credits_enterprise: int = 250
    plan_credits_supplier: int = 100
    credit_cost_deep_analysis: int = 1
    credit_cost_assistant_answer: int = 1

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
    # Touched every worker_heartbeat_seconds while the worker runs; the container health check reads its age.
    worker_heartbeat_file: str = "/tmp/worker-heartbeat"
    worker_heartbeat_seconds: float = 10.0
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

    # --- Suppliers, RFQs & comparisons ---
    rfq_number_prefix: str = "RFQ"
    supplier_code_prefix: str = "SUP"
    default_currency: str = "EUR"
    org_display_name: str = ""  # overrides the organization name on RFQ documents when set
    comparison_outlier_ratio: float = 0.5  # a price this far below the median is flagged as an outlier
    fx_max_age_days: int = 30  # an exchange rate older than this on the analysis date is flagged as stale
    quote_validity_warning_days: int = 7  # a quote expiring within this many days is flagged
    quote_import_max_mb: int = 5

    # --- Sourcing agent ---
    agent_mode: str = "auto"  # auto (model when one is configured, else deterministic) | deterministic | llm
    agent_max_question_chars: int = 4000
    agent_max_tool_calls: int = 4
    agent_history_turns: int = 6
    agent_rate_per_minute: int = 20
    agent_index_chunk_chars: int = 1500
    agent_index_on_demand_docs: int = 20  # processed documents indexed during a search if the worker has not yet

    # --- Monitoring ---
    metrics_token: str = ""  # bearer token for /metrics; when empty, /metrics is disabled in production
    metrics_dir: str = "/tmp/copilot-metrics"  # per-process snapshots merged at scrape time

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
