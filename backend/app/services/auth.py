import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import security as sec
from app.audit import record
from app.config import get_settings
from app.db import set_system, set_tenant
from app.errors import ApiError, bad_request, conflict, forbidden, unauthorized
from app.models import (
    AuthSession,
    EmailToken,
    Invitation,
    MfaRecoveryCode,
    Organization,
    User,
)
from app.rbac import Role

settings = get_settings()

VERIFY = "verify_email"
RESET = "reset_password"


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ClientInfo:
    ip: str | None
    user_agent: str | None


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass
class LoginResult:
    tokens: Tokens | None = None
    mfa_token: str | None = None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    return f"{base}-{secrets.token_hex(3)}"


# --- Email tokens ------------------------------------------------------------
async def create_email_token(db: AsyncSession, user: User, purpose: str, ttl: timedelta) -> str:
    await db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=utcnow())
    )
    raw = sec.new_opaque_token()
    db.add(
        EmailToken(
            organization_id=user.organization_id,
            user_id=user.id,
            purpose=purpose,
            token_hash=sec.sha256_hex(raw),
            expires_at=utcnow() + ttl,
        )
    )
    return raw


async def _redeem_email_token(db: AsyncSession, raw: str, purpose: str) -> tuple[EmailToken, User]:
    await set_system(db)
    token = await db.scalar(
        select(EmailToken)
        .where(
            EmailToken.token_hash == sec.sha256_hex(raw),
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
            EmailToken.expires_at > utcnow(),
        )
        .with_for_update()
    )
    if token is None:
        raise bad_request("invalid_token", "This link is invalid or has expired")
    user = await db.get(User, token.user_id)
    if user is None:
        raise bad_request("invalid_token", "This link is invalid or has expired")
    return token, user


# --- Registration & verification --------------------------------------------
async def register(
    db: AsyncSession, *, organization_name: str | None, full_name: str, email: str, password: str,
    client: ClientInfo, account_type: str = "org_buyer",
) -> tuple[User, str | None]:
    await set_system(db)
    email = normalize_email(email)
    if await db.scalar(select(User.id).where(User.email == email)):
        raise conflict("email_taken", "An account with this email already exists")
    org_name = (organization_name or "").strip() or full_name.strip()
    org = Organization(name=org_name, slug=slugify(org_name), account_type=account_type)
    db.add(org)
    await db.flush()
    user = User(
        organization_id=org.id,
        email=email,
        password_hash=sec.hash_password(password),
        full_name=full_name.strip(),
        role=Role.ADMIN.value,
        email_verified_at=None if settings.require_email_verification else utcnow(),
    )
    db.add(user)
    await db.flush()
    raw = None
    if settings.require_email_verification:
        raw = await create_email_token(db, user, VERIFY, timedelta(hours=settings.verify_token_hours))
    record(db, org_id=org.id, user_id=user.id, action="auth.register", ip=client.ip,
           meta={"account_type": account_type})
    await db.refresh(user)
    await db.commit()
    return user, raw


async def verify_email(db: AsyncSession, raw: str, client: ClientInfo) -> None:
    token, user = await _redeem_email_token(db, raw, VERIFY)
    now = utcnow()
    token.used_at = now
    user.email_verified_at = user.email_verified_at or now
    record(db, org_id=user.organization_id, user_id=user.id, action="auth.email_verified", ip=client.ip)
    await db.commit()


async def resend_verification(db: AsyncSession, email: str) -> tuple[User, str] | None:
    await set_system(db)
    user = await db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.is_active or user.email_verified_at is not None:
        await db.rollback()
        return None
    raw = await create_email_token(db, user, VERIFY, timedelta(hours=settings.verify_token_hours))
    await db.commit()
    return user, raw


# --- Login -------------------------------------------------------------------
def _assert_not_locked(user: User) -> None:
    if user.locked_until and user.locked_until > utcnow():
        minutes = max(1, int((user.locked_until - utcnow()).total_seconds() // 60) + 1)
        raise ApiError(
            429,
            "account_locked",
            f"Too many failed attempts. Try again in about {minutes} minute(s).",
            {"Retry-After": str(minutes * 60)},
        )


async def _register_failure(db: AsyncSession, user: User, client: ClientInfo, reason: str) -> None:
    user.failed_login_count += 1
    locked = user.failed_login_count >= settings.max_failed_logins
    if locked:
        user.locked_until = utcnow() + timedelta(minutes=settings.lockout_minutes)
        user.failed_login_count = 0
    record(
        db, org_id=user.organization_id, user_id=user.id, action="auth.login_failed",
        ip=client.ip, meta={"reason": reason, "locked": locked},
    )
    await db.commit()
    raise unauthorized("Invalid credentials", "invalid_credentials")


async def issue_session(db: AsyncSession, user: User, client: ClientInfo) -> Tokens:
    raw = sec.new_opaque_token()
    now = utcnow()
    session = AuthSession(
        organization_id=user.organization_id,
        user_id=user.id,
        token_hash=sec.sha256_hex(raw),
        user_agent=(client.user_agent or "")[:300] or None,
        ip=client.ip,
        expires_at=now + timedelta(days=settings.refresh_token_days),
        rotated_at=now,
        last_used_at=now,
    )
    db.add(session)
    await db.flush()
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    access = sec.create_access_token(user.id, user.organization_id, user.role, session.id)
    return Tokens(access, raw, settings.access_token_minutes * 60)


async def login(db: AsyncSession, email: str, password: str, client: ClientInfo) -> LoginResult:
    await set_system(db)
    user = await db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        sec.verify_password(sec.DUMMY_HASH, password)  # equalise timing
        raise unauthorized("Invalid credentials", "invalid_credentials")
    _assert_not_locked(user)
    if not sec.verify_password(user.password_hash, password):
        await _register_failure(db, user, client, "bad_password")
    if not user.is_active:
        raise forbidden("This account has been disabled", "account_disabled")
    if settings.require_email_verification and user.email_verified_at is None:
        raise forbidden("Verify your email address before signing in", "email_not_verified")
    if sec.password_needs_rehash(user.password_hash):
        user.password_hash = sec.hash_password(password)
    if user.mfa_enabled:
        await db.commit()
        return LoginResult(mfa_token=sec.create_mfa_token(user.id, user.organization_id))
    tokens = await issue_session(db, user, client)
    record(db, org_id=user.organization_id, user_id=user.id, action="auth.login", ip=client.ip)
    await db.commit()
    return LoginResult(tokens=tokens)


async def _check_second_factor(db: AsyncSession, user: User, code: str) -> bool:
    code = code.strip().lower()
    if "-" in code:
        recovery = await db.scalar(
            select(MfaRecoveryCode)
            .where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.code_hash == sec.sha256_hex(code),
                MfaRecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
        if recovery is None:
            return False
        recovery.used_at = utcnow()
        return True
    if not user.mfa_secret_enc:
        return False
    step = sec.verify_totp(sec.decrypt_secret(user.mfa_secret_enc), code, user.mfa_last_step)
    if step is None:
        return False
    user.mfa_last_step = step
    return True


async def complete_mfa_login(
    db: AsyncSession, mfa_token: str, code: str, client: ClientInfo
) -> Tokens:
    try:
        claims = sec.decode_token(mfa_token, "mfa")
        user_id = uuid.UUID(claims["sub"])
    except (sec.TokenError, KeyError, ValueError):
        raise unauthorized("MFA session expired. Sign in again.", "invalid_mfa_token") from None
    await set_system(db)
    user = await db.get(User, user_id)
    if user is None or not user.is_active or not user.mfa_enabled:
        raise unauthorized("MFA session expired. Sign in again.", "invalid_mfa_token")
    _assert_not_locked(user)
    if not await _check_second_factor(db, user, code):
        await _register_failure(db, user, client, "bad_mfa_code")
    tokens = await issue_session(db, user, client)
    record(db, org_id=user.organization_id, user_id=user.id, action="auth.login_mfa", ip=client.ip)
    await db.commit()
    return tokens


# --- Sessions ----------------------------------------------------------------
async def revoke_user_sessions(
    db: AsyncSession, user_id: uuid.UUID, reason: str, except_id: uuid.UUID | None = None
) -> None:
    stmt = update(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if except_id is not None:
        stmt = stmt.where(AuthSession.id != except_id)
    await db.execute(stmt.values(revoked_at=utcnow(), revoked_reason=reason))


async def refresh(db: AsyncSession, raw: str, client: ClientInfo) -> Tokens:
    await set_system(db)
    digest = sec.sha256_hex(raw)
    session = await db.scalar(
        select(AuthSession)
        .where((AuthSession.token_hash == digest) | (AuthSession.prev_token_hash == digest))
        .with_for_update()
    )
    now = utcnow()
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise unauthorized("Session expired. Sign in again.", "invalid_refresh_token")
    if session.token_hash != digest:
        # A rotated-out token was presented: either a benign race or token theft.
        if (now - session.rotated_at).total_seconds() > settings.refresh_reuse_grace_seconds:
            session.revoked_at = now
            session.revoked_reason = "reuse_detected"
            record(
                db, org_id=session.organization_id, user_id=session.user_id,
                action="auth.refresh_reuse_detected", ip=client.ip,
            )
            await db.commit()
        raise unauthorized("Session expired. Sign in again.", "invalid_refresh_token")
    user = await db.get(User, session.user_id)
    if user is None or not user.is_active:
        session.revoked_at = now
        session.revoked_reason = "user_inactive"
        await db.commit()
        raise unauthorized("Session expired. Sign in again.", "invalid_refresh_token")
    new_raw = sec.new_opaque_token()
    session.prev_token_hash = session.token_hash
    session.token_hash = sec.sha256_hex(new_raw)
    session.rotated_at = now
    session.last_used_at = now
    session.expires_at = now + timedelta(days=settings.refresh_token_days)
    session.ip = client.ip or session.ip
    access = sec.create_access_token(user.id, user.organization_id, user.role, session.id)
    await db.commit()
    return Tokens(access, new_raw, settings.access_token_minutes * 60)


async def logout(db: AsyncSession, user: User, session: AuthSession, client: ClientInfo) -> None:
    session.revoked_at = utcnow()
    session.revoked_reason = "logout"
    record(db, org_id=user.organization_id, user_id=user.id, action="auth.logout", ip=client.ip)
    await db.commit()


async def logout_all(db: AsyncSession, user: User, client: ClientInfo) -> None:
    await revoke_user_sessions(db, user.id, "logout_all")
    record(db, org_id=user.organization_id, user_id=user.id, action="auth.logout_all", ip=client.ip)
    await db.commit()


# --- Passwords ---------------------------------------------------------------
async def request_password_reset(db: AsyncSession, email: str) -> tuple[User, str] | None:
    await set_system(db)
    user = await db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.is_active:
        await db.rollback()
        return None
    raw = await create_email_token(db, user, RESET, timedelta(minutes=settings.reset_token_minutes))
    await db.commit()
    return user, raw


async def reset_password(db: AsyncSession, raw: str, new_password: str, client: ClientInfo) -> None:
    token, user = await _redeem_email_token(db, raw, RESET)
    now = utcnow()
    token.used_at = now
    user.password_hash = sec.hash_password(new_password)
    user.password_changed_at = now
    user.failed_login_count = 0
    user.locked_until = None
    user.email_verified_at = user.email_verified_at or now
    await revoke_user_sessions(db, user.id, "password_reset")
    record(db, org_id=user.organization_id, user_id=user.id, action="auth.password_reset", ip=client.ip)
    await db.commit()


async def change_password(
    db: AsyncSession, user: User, session: AuthSession, current: str, new: str, client: ClientInfo
) -> None:
    if not sec.verify_password(user.password_hash, current):
        raise ApiError(400, "invalid_password", "Current password is incorrect")
    user.password_hash = sec.hash_password(new)
    user.password_changed_at = utcnow()
    await revoke_user_sessions(db, user.id, "password_changed", except_id=session.id)
    record(db, org_id=user.organization_id, user_id=user.id, action="auth.password_changed", ip=client.ip)
    await db.commit()


# --- MFA ---------------------------------------------------------------------
async def mfa_setup(db: AsyncSession, user: User) -> tuple[str, str]:
    if user.mfa_enabled:
        raise conflict("mfa_already_enabled", "Two-factor authentication is already enabled")
    secret = sec.new_totp_secret()
    user.mfa_pending_secret_enc = sec.encrypt_secret(secret)
    await db.commit()
    return secret, sec.totp_uri(secret, user.email)


async def mfa_enable(db: AsyncSession, user: User, code: str, client: ClientInfo) -> list[str]:
    if user.mfa_enabled:
        raise conflict("mfa_already_enabled", "Two-factor authentication is already enabled")
    if not user.mfa_pending_secret_enc:
        raise bad_request("mfa_not_started", "Start two-factor setup first")
    step = sec.verify_totp(sec.decrypt_secret(user.mfa_pending_secret_enc), code.strip(), 0)
    if step is None:
        raise bad_request("invalid_code", "That code is not valid")
    user.mfa_secret_enc = user.mfa_pending_secret_enc
    user.mfa_pending_secret_enc = None
    user.mfa_enabled = True
    user.mfa_last_step = step
    codes = sec.new_recovery_codes()
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    for c in codes:
        db.add(
            MfaRecoveryCode(
                organization_id=user.organization_id, user_id=user.id, code_hash=sec.sha256_hex(c)
            )
        )
    record(db, org_id=user.organization_id, user_id=user.id, action="auth.mfa_enabled", ip=client.ip)
    await db.commit()
    return codes


async def mfa_disable(
    db: AsyncSession, user: User, password: str, code: str, client: ClientInfo
) -> None:
    if not user.mfa_enabled:
        raise bad_request("mfa_not_enabled", "Two-factor authentication is not enabled")
    if not sec.verify_password(user.password_hash, password):
        raise ApiError(400, "invalid_password", "Password is incorrect")
    if not await _check_second_factor(db, user, code):
        await db.rollback()
        raise bad_request("invalid_code", "That code is not valid")
    user.mfa_enabled = False
    user.mfa_secret_enc = None
    user.mfa_pending_secret_enc = None
    user.mfa_last_step = 0
    await db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    record(db, org_id=user.organization_id, user_id=user.id, action="auth.mfa_disabled", ip=client.ip)
    await db.commit()


# --- Invitations -------------------------------------------------------------
async def accept_invitation(
    db: AsyncSession, raw: str, full_name: str, password: str, client: ClientInfo
) -> User:
    await set_system(db)
    invite = await db.scalar(
        select(Invitation)
        .where(
            Invitation.token_hash == sec.sha256_hex(raw),
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > utcnow(),
        )
        .with_for_update()
    )
    if invite is None:
        raise bad_request("invalid_token", "This invitation is invalid or has expired")
    if await db.scalar(select(User.id).where(User.email == invite.email)):
        raise conflict("email_taken", "An account with this email already exists")
    now = utcnow()
    user = User(
        organization_id=invite.organization_id,
        email=invite.email,
        password_hash=sec.hash_password(password),
        full_name=full_name.strip(),
        role=invite.role,
        email_verified_at=now,  # the invitation link proves control of the mailbox
    )
    db.add(user)
    await db.flush()
    invite.accepted_at = now
    record(
        db, org_id=invite.organization_id, user_id=user.id, action="invitation.accepted",
        ip=client.ip, meta={"role": invite.role},
    )
    await db.refresh(user)
    await db.commit()
    return user


async def email_in_use(db: AsyncSession, email: str, org_id: uuid.UUID) -> bool:
    """Global uniqueness check from inside a tenant-scoped request."""
    await set_system(db)
    exists = await db.scalar(select(User.id).where(User.email == email)) is not None
    await set_tenant(db, org_id)
    return exists
