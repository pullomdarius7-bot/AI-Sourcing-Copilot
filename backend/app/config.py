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
    # Pay-as-you-go top-ups for subscribers who run out: price per credit by plan, pack size limits, and how low
    # the balance must be before a top-up can be bought.
    # Priced at the plan's own per-credit rate (79/25, 249/250, 149/100), rounded down: running out never costs more.
    topup_price_cents_solo: int = 299
    topup_price_cents_enterprise: int = 99
    topup_price_cents_supplier: int = 149
    topup_min_credits: int = 5
    topup_max_credits: int = 500
    topup_available_at_or_below: int = 5
    # People (active users plus open invitations) an organization buyer account may have before it subscribes.
    trial_seats: int = 5
    # Back office: platform admins must have two-factor sign-in on (strongly recommended on any public server).
    platform_admin_requires_mfa: bool = True
    # Where customers write for help; Sourcing Team requests are marked as priority.
    support_email: str = ""

    # --- AI sourcing engine: web supplier discovery ---
    # searxng (self-hosted, SEARXNG_URL) | brave (Brave Search API, BRAVE_API_KEY) | empty = web search off
    search_provider: str = ""
    searxng_url: str = ""
    brave_api_key: str = ""
    credit_cost_web_sourcing: int = 2
    web_sourcing_max_sites: int = 5  # supplier websites read per search
    web_sourcing_pages_per_site: int = 3
    web_fetch_timeout_seconds: float = 10.0
    web_fetch_max_bytes: int = 1_500_000
    web_allow_private_hosts: bool = False  # tests only: never point the crawler at internal addresses in production
    crawler_user_agent: str = "SourcingCopilotBot/1.0"

    # --- Supplier invites sent to businesses the sourcing engine finds ---
    # auto = send when every safeguard passes | review = record, send nothing | off = do not record
    supplier_invites: str = "auto"
    invite_cooldown_days: int = 90
    invite_daily_limit: int = 200
    invites_per_request: int = 3  # only the most relevant suppliers are invited
    # Suppliers who join through an invitation get this much off their first paid month (Stripe coupon, created on
    # first use with this id when it does not exist yet).
    invite_first_month_discount_pct: int = 75
    stripe_invite_coupon_id: str = "invited-supplier-first-month"
    # Required by anti-spam law (CAN-SPAM): the sender's postal address. Invites are held until it is set.
    invite_sender_postal_address: str = ""
    platform_name: str = "Sourcing Copilot"

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
    job_cancel_poll_seconds: float = 3.0  # how soon a running job notices it was stopped
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
    anthropic_effort: str = "medium"  # low | medium | high | xhigh | max: how hard the model works per call
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    local_llm_url: str = ""  # Ollama base URL, e.g. http://ollama:11434
    local_llm_model: str = "llama3.1:8b"
    # A model on the server's own CPU is far slower than a hosted one and only reads what fits in its context
    # window (Ollama's default is 2048 tokens, which silently cuts long documents short).
    local_llm_timeout_seconds: float = 900.0
    local_llm_num_ctx: int = 8192
    local_llm_max_input_chars: int = 12000  # caps document text sent to every provider while a local model is on
    local_llm_max_output_tokens: int = 4096

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
