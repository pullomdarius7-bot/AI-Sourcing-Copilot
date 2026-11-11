import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TenantMixin:
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    # solo_buyer | org_buyer | supplier (app.onboarding.AccountType)
    account_type: Mapped[str] = mapped_column(String(12), nullable=False, default="org_buyer")
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Intake answers, validated against the account type's form before they are stored.
    onboarding_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Analysis credits left. Changed only by app.billing.ledger, together with a credit_transactions row.
    credit_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The part of credit_balance bought as top-ups: spent last, kept at renewal and on cancellation.
    purchased_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Sourcing Team margin rules and freight overlays used as the defaults of every deep analysis.
    analysis_defaults: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # invite_first_month: joined through a supplier invitation, first month discounted | used
    signup_promo: Mapped[str | None] = mapped_column(String(24))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64))
    plan: Mapped[str | None] = mapped_column(String(40))  # key of app.billing.plans.PLANS
    # none | trialing | active | past_due | cancelled
    subscription_status: Mapped[str] = mapped_column(String(12), nullable=False, default="none")
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(TenantMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_secret_enc: Mapped[str | None] = mapped_column(Text)
    mfa_pending_secret_enc: Mapped[str | None] = mapped_column(Text)
    mfa_last_step: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = _created()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None


class AuthSession(TenantMixin, Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    prev_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    rotated_at: Mapped[datetime] = _created()
    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(40))


class EmailToken(TenantMixin, Base):
    __tablename__ = "email_tokens"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class MfaRecoveryCode(TenantMixin, Base):
    __tablename__ = "mfa_recovery_codes"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Invitation(TenantMixin, Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class AuditLog(TenantMixin, Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()


class CreditTransaction(TenantMixin, Base):
    """Append-only record of every credit granted or spent (one credit = one manifest analysis)."""

    __tablename__ = "credit_transactions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    # trial_grant | plan_grant | purchase | usage | refund | adjustment | expiry
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(300))
    # What was charged for: the metered action and the document, run or answer it paid for.
    action: Mapped[str | None] = mapped_column(String(40))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # held (charged, work in flight) | settled | refunded. The only column the application may update.
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="settled")
    created_at: Mapped[datetime] = _created()


class Document(TenantMixin, Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _pk()
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_backend: Mapped[str | None] = mapped_column(String(20))
    storage_key: Mapped[str | None] = mapped_column(String(300))
    # sourcing (documents, quotes, RFQs) | manifest (a supplier's marketplace manifest)
    purpose: Mapped[str] = mapped_column(String(12), nullable=False, default="sourcing")
    # uploaded | scanning | extracting | analyzing | processed | quarantined | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    scan_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    scan_engine: Mapped[str | None] = mapped_column(String(60))
    scan_detail: Mapped[str | None] = mapped_column(String(200))
    # pending | succeeded | needs_review | skipped | failed
    analysis_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    file_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentExtraction(TenantMixin, Base):
    __tablename__ = "document_extractions"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class AiAuditLog(TenantMixin, Base):
    __tablename__ = "ai_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(String(60), nullable=False)
    # ok | invalid_output | provider_error | skipped
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_sha256: Mapped[str | None] = mapped_column(String(64))
    input_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _created()


class Job(TenantMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _pk()
    queue: Mapped[str] = mapped_column(String(40), nullable=False, default="default")
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    # queued | running | succeeded | dead
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)  # higher runs first
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    run_at: Mapped[datetime] = _created()
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtractionRun(TenantMixin, Base):
    __tablename__ = "extraction_runs"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # extracting | pending_review | ready_for_approval | approved | rejected | failed | superseded
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="extracting")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    source_text_sha256: Mapped[str | None] = mapped_column(String(64))
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceMixin:
    """Every extracted item carries a source reference, a confidence and a validation state."""

    source_quote: Mapped[str | None] = mapped_column(Text)
    source_start: Mapped[int | None] = mapped_column(Integer)
    source_end: Mapped[int | None] = mapped_column(Integer)
    source_line: Mapped[int | None] = mapped_column(Integer)
    source_section: Mapped[str | None] = mapped_column(String(200))
    model_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence_detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # grounded (quote located in the document) | unknown (nothing stated) | manual (human-supplied evidence)
    grounding_status: Mapped[str] = mapped_column(String(12), nullable=False, default="grounded")
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # unverified | verified | corrected | rejected
    validation_state: Mapped[str] = mapped_column(String(12), nullable=False, default="unverified")
    verified_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    original: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Requirement(EvidenceMixin, TenantMixin, Base):
    __tablename__ = "requirements"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    priority: Mapped[str] = mapped_column(String(12), nullable=False, default="unknown")


class Parameter(EvidenceMixin, TenantMixin, Base):
    __tablename__ = "parameters"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extraction_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirements.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    value_type: Mapped[str] = mapped_column(String(8), nullable=False, default="text")
    value_text: Mapped[str | None] = mapped_column(Text)
    value_number: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(30))
    qualifier: Mapped[str | None] = mapped_column(String(8))
    unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ReviewEvent(TenantMixin, Base):
    """Append-only history of everything that happens to an extraction run and its items."""

    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_type: Mapped[str] = mapped_column(String(12), nullable=False)  # run | requirement | parameter
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str | None] = mapped_column(String(24))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


# --- Suppliers ---------------------------------------------------------------------------------------
class SupplierCategory(TenantMixin, Base):
    __tablename__ = "supplier_categories"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("supplier_categories.id"))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Supplier(TenantMixin, Base):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(300))
    # prospect | under_review | qualified | disqualified | blocked
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="prospect")
    country: Mapped[str | None] = mapped_column(String(2))
    website: Mapped[str | None] = mapped_column(String(300))
    tax_id: Mapped[str | None] = mapped_column(String(60))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    payment_terms: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str | None] = mapped_column(String(3))
    typical_lead_time_days: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class SupplierCategoryLink(TenantMixin, Base):
    __tablename__ = "supplier_category_links"

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_categories.id", ondelete="CASCADE"), primary_key=True
    )


class SupplierContact(TenantMixin, Base):
    __tablename__ = "supplier_contacts"

    id: Mapped[uuid.UUID] = _pk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(60))
    role: Mapped[str | None] = mapped_column(String(120))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created()


class QualificationCriterion(TenantMixin, Base):
    __tablename__ = "qualification_criteria"

    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created()


class SupplierQualificationCheck(TenantMixin, Base):
    __tablename__ = "supplier_qualification_checks"

    id: Mapped[uuid.UUID] = _pk()
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    criterion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("qualification_criteria.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")  # pending | passed | failed | waived
    note: Mapped[str | None] = mapped_column(Text)
    evidence_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[Any] = mapped_column(Date)
    checked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- RFQs ---------------------------------------------------------------------------------------------
class Rfq(TenantMixin, Base):
    __tablename__ = "rfqs"

    id: Mapped[uuid.UUID] = _pk()
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # draft | pending_approval | approved | sent | closed | awarded | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("extraction_runs.id"))
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("supplier_categories.id"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    response_due_date: Mapped[Any] = mapped_column(Date)
    message: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    awarded_supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    award_comparison_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    awarded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    awarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    award_note: Mapped[str | None] = mapped_column(Text)
    award_recommendation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    required_by: Mapped[Any] = mapped_column(Date)  # delivery needed by; drives lead-time analysis
    requested_incoterms: Mapped[str | None] = mapped_column(String(10))
    requested_payment_terms: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class RfqItem(TenantMixin, Base):
    __tablename__ = "rfq_items"

    id: Mapped[uuid.UUID] = _pk()
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="requirement")  # requirement | manual
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    priority: Mapped[str] = mapped_column(String(12), nullable=False, default="unknown")
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(30))
    parameters: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    source_requirement_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()


class RfqSupplier(TenantMixin, Base):
    __tablename__ = "rfq_suppliers"

    id: Mapped[uuid.UUID] = _pk()
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    # invited | sending | sent | failed | quoted | declined
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="invited")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class RfqPackage(TenantMixin, Base):
    __tablename__ = "rfq_packages"

    id: Mapped[uuid.UUID] = _pk()
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    rfq_version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    to_addresses: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    send_status: Mapped[str] = mapped_column(String(10), nullable=False, default="not_sent")  # not_sent | queued | sent | failed
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class RfqQuote(TenantMixin, Base):
    __tablename__ = "rfq_quotes"

    id: Mapped[uuid.UUID] = _pk()
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="manual")  # analysis | manual | import
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")  # draft | verified
    currency: Mapped[str | None] = mapped_column(String(3))
    incoterms: Mapped[str | None] = mapped_column(String(10))
    payment_terms: Mapped[str | None] = mapped_column(String(200))
    valid_until: Mapped[Any] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class RfqQuoteLine(TenantMixin, Base):
    __tablename__ = "rfq_quote_lines"

    id: Mapped[uuid.UUID] = _pk()
    quote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfq_quotes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rfq_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    match_status: Mapped[str] = mapped_column(String(10), nullable=False, default="unmatched")  # unmatched | suggested | confirmed
    match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(30))
    unit_price: Mapped[float | None] = mapped_column(Float)
    total_price: Mapped[float | None] = mapped_column(Float)
    lead_time_days: Mapped[float | None] = mapped_column(Float)
    moq: Mapped[float | None] = mapped_column(Float)  # minimum order quantity, in the line's unit
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_quote: Mapped[str | None] = mapped_column(Text)


class Comparison(TenantMixin, Base):
    """Immutable record of one comparison run: inputs, weights and result."""

    __tablename__ = "comparisons"

    id: Mapped[uuid.UUID] = _pk()
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    inputs_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()


class AwardRecommendation(TenantMixin, Base):
    """A buyer's proposed award, decided by a separate approver. Approval is what awards the RFQ."""

    __tablename__ = "award_recommendations"

    id: Mapped[uuid.UUID] = _pk()
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    comparison_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("comparisons.id"), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")  # pending | approved | rejected | withdrawn
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = _created()
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)


class FxRate(TenantMixin, Base):
    """Exchange rate recorded by the organization: 1 base = rate quote, valid from a date. Never fetched."""

    __tablename__ = "fx_rates"

    id: Mapped[uuid.UUID] = _pk()
    base: Mapped[str] = mapped_column(String(3), nullable=False)
    quote: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    valid_from: Mapped[Any] = mapped_column(Date, nullable=False)
    source: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created()


class UnitConversion(TenantMixin, Base):
    """Organization rule: 1 from_unit = factor to_unit (e.g. 1 box = 50 pcs, 1 pcs = 0.12 kg)."""

    __tablename__ = "unit_conversions"

    id: Mapped[uuid.UUID] = _pk()
    from_unit: Mapped[str] = mapped_column(String(30), nullable=False)
    to_unit: Mapped[str] = mapped_column(String(30), nullable=False)
    factor: Mapped[Decimal] = mapped_column(Numeric(30, 12), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created()


class DomainEvent(TenantMixin, Base):
    """Append-only history for suppliers, RFQs, quotes and comparisons."""

    __tablename__ = "domain_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str | None] = mapped_column(String(24))
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


# --- Sourcing agent ---------------------------------------------------------------------------------------------
class KnowledgeChunk(TenantMixin, Base):
    """Full-text searchable slice of a processed document's text (column tsv is generated by PostgreSQL)."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_no: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()


class AgentConversation(TenantMixin, Base):
    __tablename__ = "agent_conversations"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class AgentRun(TenantMixin, Base):
    """Append-only trace of one agent answer: plan, tool calls, model calls, safety checks, sources."""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # deterministic | llm
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # answered | fallback | refused | clarify | error
    intent: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    plan: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    tool_calls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    llm_calls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    safety: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    answer_sha256: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class AgentMessage(TenantMixin, Base):
    __tablename__ = "agent_messages"

    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    blocks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    unknowns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    follow_ups: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    sources: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"))
    feedback: Mapped[int | None] = mapped_column(SmallInteger)
    feedback_note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = _created()


class AgentReport(TenantMixin, Base):
    __tablename__ = "agent_reports"

    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_conversations.id", ondelete="SET NULL")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", deferrable=True, initially="DEFERRED")
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    blocks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    unknowns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    sources: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created()


# --- Marketplace ----------------------------------------------------------------------------------------------
class MarketProfile(Base):
    """The masked public face of an account in the marketplace. Readable by every account."""

    __tablename__ = "market_profiles"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    alias: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # buyer | seller
    label: Mapped[str] = mapped_column(String(40), nullable=False)  # Liquidator, Wholesaler, Reseller, Buying team
    hub_region: Mapped[str | None] = mapped_column(String(12))
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_deals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    member_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = _created()


class MarketplaceLot(TenantMixin, Base):
    """A supplier's lot: the normalized manifest plus the listing details. Visible to every account while
    published; holds no contact details (those live in LotPrivateDetails)."""

    __tablename__ = "marketplace_lots"

    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    default_condition: Mapped[str | None] = mapped_column(String(20))
    ingest_status: Mapped[str] = mapped_column(String(12), nullable=False, default="processing")  # processing | ready | failed
    ingest_method: Mapped[str | None] = mapped_column(String(8))  # rules | ai
    ingest_error: Mapped[str | None] = mapped_column(Text)
    ingest_flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")  # draft | published | sold | withdrawn
    # A buyer's own manifest (from an outside auction or supplier): analysed privately, never listed.
    private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # run this analysis once normalized
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text)
    lot_type: Mapped[str | None] = mapped_column(String(14))  # single_pallet | multi_pallet | truckload
    pallet_count: Mapped[int | None] = mapped_column(Integer)
    asking_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # Buy-It-Now
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    dispatch_zip: Mapped[str | None] = mapped_column(String(5))
    region: Mapped[str | None] = mapped_column(String(12))
    liftgate_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dock_high_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    local_pickup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_msrp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    est_resale: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    est_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    primary_category: Mapped[str | None] = mapped_column(String(20))
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False, default=list)
    conditions: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False, default=list)
    condition_mix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    category_mix: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class LotItem(TenantMixin, Base):
    __tablename__ = "lot_items"

    id: Mapped[uuid.UUID] = _pk()
    lot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("marketplace_lots.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_line: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    condition: Mapped[str] = mapped_column(String(20), nullable=False)
    upc: Mapped[str | None] = mapped_column(String(14))
    asin: Mapped[str | None] = mapped_column(String(10))
    model_number: Mapped[str | None] = mapped_column(String(60))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_msrp: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # wholesale asking per unit, if the manifest has it
    method: Mapped[str] = mapped_column(String(8), nullable=False)  # rules | ai
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class LotPrivateDetails(TenantMixin, Base):
    """Seller-only: never readable by another account (released to the buyer only when an offer is accepted)."""

    __tablename__ = "lot_private_details"

    lot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("marketplace_lots.id", ondelete="CASCADE"), primary_key=True
    )
    reserve_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    pickup_address: Mapped[str | None] = mapped_column(Text)
    pickup_contact: Mapped[str | None] = mapped_column(String(200))
    pickup_phone: Mapped[str | None] = mapped_column(String(40))
    pickup_notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = _created()


class LotAnalysis(TenantMixin, Base):
    """A buyer's paid deep analysis of a marketplace lot. Belongs to the buyer."""

    __tablename__ = "lot_analyses"

    id: Mapped[uuid.UUID] = _pk()
    lot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("marketplace_lots.id", ondelete="CASCADE"), nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")  # queued | running | succeeded | failed
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    method: Mapped[str | None] = mapped_column(String(8))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RfqThread(Base):
    """A negotiation between one buyer and one seller, about a lot or about a quote on a buyer request.
    Readable only by the two parties."""

    __tablename__ = "rfq_threads"

    id: Mapped[uuid.UUID] = _pk()
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("marketplace_lots.id", ondelete="CASCADE"))
    buyer_org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    supplier_org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    lot_title: Mapped[str] = mapped_column(String(200), nullable=False)  # kept even if the lot is withdrawn later
    # Set when a supplier proposes a lot in answer to a buyer's sourcing request.
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sourcing_requests.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")  # open | accepted | declined | withdrawn | closed
    closed_reason: Mapped[str | None] = mapped_column(String(40))
    offer_by: Mapped[str | None] = mapped_column(String(8))  # buyer | supplier: who made the offer on the table
    offer_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    shipping_terms: Mapped[str | None] = mapped_column(String(16))  # buyer_freight | seller_freight
    target_close_date: Mapped[Any] = mapped_column(Date)
    custom_terms: Mapped[str | None] = mapped_column(Text)
    agreed_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    revealed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # contact details, released on acceptance
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime] = _created()
    created_at: Mapped[datetime] = _created()


class RfqThreadEvent(Base):
    """Append-only, hash-chained record of every offer, counteroffer, decision and message."""

    __tablename__ = "rfq_thread_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rfq_threads.id", ondelete="CASCADE"), nullable=False)
    buyer_org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    supplier_org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_side: Mapped[str] = mapped_column(String(8), nullable=False)  # buyer | supplier | system
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # offer | counter | accept | decline | withdraw | message | auto_decline | closed
    kind: Mapped[str] = mapped_column(String(14), nullable=False)
    offer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    body: Mapped[str | None] = mapped_column(Text)
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RfqModeration(TenantMixin, Base):
    """What a participant actually typed before contact details were removed. Only the author's account sees it."""

    __tablename__ = "rfq_moderation"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rfq_threads.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    original: Mapped[str] = mapped_column(Text, nullable=False)
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()


# --- AI sourcing engine ---------------------------------------------------------------------------------------
class SourcingRequest(TenantMixin, Base):
    """A buyer looking for a product. Matched against the marketplace first, then searched for on the web.
    While open, suppliers on the platform see it (masked) as a lead."""

    __tablename__ = "sourcing_requests"

    id: Mapped[uuid.UUID] = _pk()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(20))
    condition: Mapped[str | None] = mapped_column(String(20))
    quantity: Mapped[int | None] = mapped_column(Integer)
    target_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    region: Mapped[str | None] = mapped_column(String(12))
    needed_by: Mapped[Any] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")  # open | closed
    open_to_suppliers: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    search_web: Mapped[str] = mapped_column(String(8), nullable=False, default="auto")  # auto | always | never
    web_status: Mapped[str] = mapped_column(String(10), nullable=False, default="none")
    web_note: Mapped[str | None] = mapped_column(Text)
    marketplace_matches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    web_matches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invites_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourcingCandidate(TenantMixin, Base):
    """One supplier found for a request: a marketplace lot, or a business found on the web."""

    __tablename__ = "sourcing_candidates"

    id: Mapped[uuid.UUID] = _pk()
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sourcing_requests.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # marketplace | web
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("marketplace_lots.id", ondelete="SET NULL"))
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    website: Mapped[str | None] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(255))
    business_type: Mapped[str | None] = mapped_column(String(20))
    location: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False, default=list)
    products: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    moq: Mapped[str | None] = mapped_column(String(200))
    price_notes: Mapped[str | None] = mapped_column(String(300))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(60))
    contact_url: Mapped[str | None] = mapped_column(String(500))
    relevance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    method: Mapped[str] = mapped_column(String(8), nullable=False, default="rules")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="new")  # new | added | dismissed
    invite_status: Mapped[str | None] = mapped_column(String(12))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"))
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # Web suppliers stay anonymous to the buyer: identity lives in the platform-only invitation record.
    domain_hash: Mapped[str | None] = mapped_column(String(64))
    invite_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("supplier_invites.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()


class GuestQuote(TenantMixin, Base):
    """A free quote an invited supplier sent without an account. Belongs to the buyer's account."""

    __tablename__ = "guest_quotes"

    id: Mapped[uuid.UUID] = _pk()
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sourcing_requests.id", ondelete="CASCADE"), nullable=False)
    invite_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("supplier_invites.id", ondelete="CASCADE"), nullable=False)
    supplier_alias: Mapped[str] = mapped_column(String(24), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity_available: Mapped[int] = mapped_column(Integer, nullable=False)
    condition: Mapped[str] = mapped_column(String(20), nullable=False)
    available_by: Mapped[Any] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="new")  # new | interested | declined | converted
    buyer_reply: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("rfq_threads.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class SupplierInvite(Base):
    """Platform-level: one record per supplier website domain found by the sourcing engine."""

    __tablename__ = "supplier_invites"

    id: Mapped[uuid.UUID] = _pk()
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    # pending | sent | held | suppressed | joined | failed
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    status_note: Mapped[str | None] = mapped_column(String(300))
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    demand: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    joined_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class EmailSuppression(Base):
    __tablename__ = "email_suppressions"

    value: Mapped[str] = mapped_column(String(320), primary_key=True)  # an address or a domain
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()


class ApiKey(TenantMixin, Base):
    """Read-only export API key (Sourcing Team). Only a hash is stored."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
