import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import email as mail
from app.billing.access import access_problem
from app.config import get_settings
from app.db import get_db
from app.deps import Principal, client_ip, get_principal
from app.errors import not_found
from app.models import AuthSession
from app.ratelimit import auth_rate_limit
from app.rbac import permissions_for
from app.schemas import (
    AcceptInviteIn,
    ChangePasswordIn,
    EmailIn,
    LoginIn,
    LoginOut,
    MeOut,
    MessageOut,
    MfaCodeIn,
    MfaDisableIn,
    MfaEnableOut,
    MfaLoginIn,
    MfaSetupOut,
    RefreshIn,
    RegisterIn,
    ResetPasswordIn,
    SessionOut,
    TokenIn,
    TokenOut,
    UserOut,
)
from app.services import auth as svc
from app.services.auth import ClientInfo

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])
public = [Depends(auth_rate_limit)]


def _client(request: Request) -> ClientInfo:
    return ClientInfo(ip=client_ip(request), user_agent=request.headers.get("user-agent"))


GENERIC = MessageOut(message="If the account exists, an email has been sent.")


@router.post("/register", response_model=UserOut, status_code=201, dependencies=public)
async def register(
    body: RegisterIn, request: Request, background: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    user, raw = await svc.register(
        db,
        organization_name=body.organization_name,
        account_type=body.account_type.value,
        full_name=body.full_name,
        email=body.email,
        password=body.password,
        client=_client(request),
    )
    if raw:
        subject, text = mail.verification_email(user.full_name, raw)
        background.add_task(mail.send_email, user.email, subject, text)
    return user


@router.post("/verify-email", response_model=MessageOut, dependencies=public)
async def verify_email(body: TokenIn, request: Request, db: AsyncSession = Depends(get_db)):
    await svc.verify_email(db, body.token, _client(request))
    return MessageOut(message="Email verified")


@router.post("/resend-verification", response_model=MessageOut, status_code=202, dependencies=public)
async def resend_verification(
    body: EmailIn, background: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    result = await svc.resend_verification(db, body.email)
    if result:
        user, raw = result
        subject, text = mail.verification_email(user.full_name, raw)
        background.add_task(mail.send_email, user.email, subject, text)
    return GENERIC


@router.post("/login", response_model=LoginOut, dependencies=public)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    result = await svc.login(db, body.email, body.password, _client(request))
    if result.mfa_token:
        return LoginOut(mfa_required=True, mfa_token=result.mfa_token)
    t = result.tokens
    return LoginOut(access_token=t.access_token, refresh_token=t.refresh_token, expires_in=t.expires_in)


@router.post("/mfa/login", response_model=TokenOut, dependencies=public)
async def mfa_login(body: MfaLoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    t = await svc.complete_mfa_login(db, body.mfa_token, body.code, _client(request))
    return TokenOut(access_token=t.access_token, refresh_token=t.refresh_token, expires_in=t.expires_in)


@router.post("/refresh", response_model=TokenOut, dependencies=public)
async def refresh(body: RefreshIn, request: Request, db: AsyncSession = Depends(get_db)):
    t = await svc.refresh(db, body.refresh_token, _client(request))
    return TokenOut(access_token=t.access_token, refresh_token=t.refresh_token, expires_in=t.expires_in)


@router.post("/logout", response_model=MessageOut)
async def logout(
    request: Request, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    await svc.logout(db, p.user, p.session, _client(request))
    return MessageOut(message="Signed out")


@router.post("/logout-all", response_model=MessageOut)
async def logout_all(
    request: Request, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    await svc.logout_all(db, p.user, _client(request))
    return MessageOut(message="Signed out everywhere")


@router.post("/password/forgot", response_model=MessageOut, status_code=202, dependencies=public)
async def forgot_password(
    body: EmailIn, background: BackgroundTasks, db: AsyncSession = Depends(get_db)
):
    result = await svc.request_password_reset(db, body.email)
    if result:
        user, raw = result
        subject, text = mail.reset_email(user.full_name, raw)
        background.add_task(mail.send_email, user.email, subject, text)
    return GENERIC


@router.post("/password/reset", response_model=MessageOut, dependencies=public)
async def reset_password(body: ResetPasswordIn, request: Request, db: AsyncSession = Depends(get_db)):
    await svc.reset_password(db, body.token, body.new_password, _client(request))
    return MessageOut(message="Password updated. Sign in with your new password.")


@router.post("/password/change", response_model=MessageOut)
async def change_password(
    body: ChangePasswordIn,
    request: Request,
    p: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    await svc.change_password(
        db, p.user, p.session, body.current_password, body.new_password, _client(request)
    )
    return MessageOut(message="Password changed. Other sessions were signed out.")


@router.post("/accept-invitation", response_model=UserOut, status_code=201, dependencies=public)
async def accept_invitation(body: AcceptInviteIn, request: Request, db: AsyncSession = Depends(get_db)):
    return await svc.accept_invitation(
        db, body.token, body.full_name, body.password, _client(request)
    )


@router.get("/me", response_model=MeOut)
async def me(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)):
    data = UserOut.model_validate(p.user).model_dump()
    return MeOut(
        **data,
        organization_name=p.org.name,
        account_type=p.org.account_type,
        onboarding_completed=p.org.onboarding_completed,
        credit_balance=p.org.credit_balance,
        trial_ends_at=p.org.trial_ends_at,
        plan=p.org.plan,
        subscription_status=p.org.subscription_status,
        ai_access=access_problem(p.org) is None,
        permissions=permissions_for(p.user.role),
    )


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)):
    from app.services.auth import utcnow

    rows = await db.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == p.user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
        .order_by(AuthSession.last_used_at.desc())
    )
    return [
        SessionOut(
            id=s.id, user_agent=s.user_agent, ip=s.ip, created_at=s.created_at,
            last_used_at=s.last_used_at, expires_at=s.expires_at, current=s.id == p.session.id,
        )
        for s in rows
    ]


@router.delete("/sessions/{session_id}", response_model=MessageOut)
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    p: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    from app.audit import record
    from app.services.auth import utcnow

    target = await db.scalar(
        select(AuthSession).where(
            AuthSession.id == session_id,
            AuthSession.user_id == p.user.id,
            AuthSession.revoked_at.is_(None),
        )
    )
    if target is None:
        raise not_found("Session not found")
    target.revoked_at = utcnow()
    target.revoked_reason = "revoked_by_user"
    record(db, org_id=p.org_id, user_id=p.user.id, action="auth.session_revoked", ip=p.ip,
           meta={"session_id": str(session_id)})
    await db.commit()
    return MessageOut(message="Session revoked")


@router.post("/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)):
    secret, uri = await svc.mfa_setup(db, p.user)
    return MfaSetupOut(secret=secret, otpauth_uri=uri)


@router.post("/mfa/enable", response_model=MfaEnableOut)
async def mfa_enable(
    body: MfaCodeIn,
    request: Request,
    p: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    codes = await svc.mfa_enable(db, p.user, body.code, _client(request))
    return MfaEnableOut(recovery_codes=codes)


@router.post("/mfa/disable", response_model=MessageOut)
async def mfa_disable(
    body: MfaDisableIn,
    request: Request,
    p: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    await svc.mfa_disable(db, p.user, body.password, body.code, _client(request))
    return MessageOut(message="Two-factor authentication disabled")
