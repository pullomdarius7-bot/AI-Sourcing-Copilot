import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.onboarding import AccountType
from app.rbac import Role
from app.security import password_problem


def _strong(value: str) -> str:
    problem = password_problem(value)
    if problem:
        raise ValueError(problem)
    return value


class RegisterIn(BaseModel):
    # A hint from the page the visitor signed up on; the role is confirmed in the onboarding wizard.
    account_type: AccountType = AccountType.SOLO_BUYER
    # Optional: an account without a company name is named after its owner.
    organization_name: str | None = Field(default=None, min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str
    # Set when a supplier signs up from an invitation the sourcing engine sent.
    invite_token: str | None = Field(default=None, max_length=100)

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        return _strong(value)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class LoginOut(BaseModel):
    mfa_required: bool = False
    mfa_token: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class MfaLoginIn(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=20)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokenIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class EmailIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def check_new_password(cls, value: str) -> str:
        return _strong(value)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def check_new_password(cls, value: str) -> str:
        return _strong(value)


class MessageOut(BaseModel):
    message: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    email_verified: bool
    mfa_enabled: bool
    last_login_at: datetime | None
    created_at: datetime


class MeOut(UserOut):
    is_platform_admin: bool = False
    organization_name: str
    account_type: AccountType
    onboarding_completed: bool
    credit_balance: int
    trial_ends_at: datetime | None
    plan: str | None
    subscription_status: str
    ai_access: bool
    billing_exempt: bool = False
    permissions: list[str]


class UserUpdateIn(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    is_active: bool | None = None


class OrgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    account_type: AccountType
    onboarding_completed: bool
    created_at: datetime


class OrgUpdateIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)


class InviteIn(BaseModel):
    email: EmailStr
    role: Role


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    password: str

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        return _strong(value)


class SessionOut(BaseModel):
    id: uuid.UUID
    user_agent: str | None
    ip: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    current: bool


class MfaSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class MfaEnableOut(BaseModel):
    recovery_codes: list[str]


class MfaDisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=20)


class AuditOut(BaseModel):
    id: int
    user_id: uuid.UUID | None
    actor_email: str | None
    action: str
    ip: str | None
    meta: dict[str, Any]
    created_at: datetime


# --- Sprint 2: documents, jobs, AI -------------------------------------------------
class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_backend: str | None
    status: str
    scan_status: str
    scan_engine: str | None
    scan_detail: str | None
    analysis_status: str
    file_metadata: dict[str, Any]
    error: str | None
    uploaded_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None


class JobBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    attempts: int
    max_attempts: int
    run_at: datetime
    last_error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class DocumentDetailOut(DocumentOut):
    text_preview: str | None
    jobs: list[JobBrief]


class UploadOut(BaseModel):
    document: DocumentOut
    duplicate: bool


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int


class UploadLimitsOut(BaseModel):
    max_upload_mb: int
    extensions: list[str]


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    provider: str
    model: str
    prompt_version: str
    result: dict[str, Any]
    flags: list[Any]
    needs_review: bool
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime


class JobOut(JobBrief):
    organization_id: uuid.UUID
    queue: str
    payload: dict[str, Any]
    locked_by: str | None
    created_at: datetime


class JobStatsOut(BaseModel):
    queued: int
    running: int
    succeeded: int
    dead: int
    cancelled: int = 0
    oldest_queued_age_seconds: int | None
    succeeded_last_hour: int
    avg_duration_ms_last_hour: int | None
    active_workers: list[str]
    by_kind: list[dict[str, Any]]


class AiAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: uuid.UUID | None
    user_id: uuid.UUID | None
    provider: str
    model: str
    purpose: str
    status: str
    prompt_sha256: str
    response_sha256: str | None
    input_chars: int
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    flags: list[Any]
    error: str | None
    created_at: datetime


class ProviderOut(BaseModel):
    name: str
    model: str
    external: bool
