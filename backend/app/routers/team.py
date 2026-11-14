import uuid
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import email as mail
from app import security as sec
from app.audit import record
from app.billing import ledger, seats
from app.config import get_settings
from app.db import get_db
from app.deps import Principal, require_permission
from app.errors import ApiError, conflict, not_found
from app.models import AuditLog, Invitation, Organization, User
from app.onboarding import AccountType
from app.rbac import Permission, Role
from app.schemas import (
    AuditOut,
    InviteIn,
    InviteOut,
    MessageOut,
    OrgOut,
    OrgUpdateIn,
    UserOut,
    UserUpdateIn,
)
from app.services import auth as svc

settings = get_settings()
router = APIRouter(tags=["team"])


# --- Organization ------------------------------------------------------------
@router.get("/organization", response_model=OrgOut)
async def get_organization(
    p: Principal = Depends(require_permission(Permission.ORG_READ)),
    db: AsyncSession = Depends(get_db),
):
    org = await db.get(Organization, p.org_id)
    if org is None:
        raise not_found("Organization not found")
    return org


@router.patch("/organization", response_model=OrgOut)
async def update_organization(
    body: OrgUpdateIn,
    p: Principal = Depends(require_permission(Permission.ORG_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    org = await db.get(Organization, p.org_id)
    if org is None:
        raise not_found("Organization not found")
    old = org.name
    org.name = body.name.strip()
    org.updated_at = svc.utcnow()
    record(db, org_id=p.org_id, user_id=p.user.id, action="organization.updated", ip=p.ip,
           meta={"from": old, "to": org.name})
    await db.commit()
    return org


# --- Users -------------------------------------------------------------------
@router.get("/seats")
async def seat_usage(
    p: Principal = Depends(require_permission(Permission.USERS_READ)), db: AsyncSession = Depends(get_db),
):
    return {"used": await seats.seats_used(db, p.org_id), "limit": seats.seat_limit(p.org)}


@router.get("/users", response_model=list[UserOut])
async def list_users(
    p: Principal = Depends(require_permission(Permission.USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(User).where(User.organization_id == p.org_id).order_by(User.created_at)
    )
    return list(rows)


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    p: Principal = Depends(require_permission(Permission.USERS_READ)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(select(User).where(User.id == user_id, User.organization_id == p.org_id))
    if user is None:
        raise not_found("User not found")
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateIn,
    p: Principal = Depends(require_permission(Permission.USERS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    user = await db.scalar(
        select(User).where(User.id == user_id, User.organization_id == p.org_id).with_for_update()
    )
    if user is None:
        raise not_found("User not found")

    new_role = body.role.value if body.role else user.role
    new_active = user.is_active if body.is_active is None else body.is_active
    removes_admin = user.role == Role.ADMIN and user.is_active and (
        new_role != Role.ADMIN or not new_active
    )
    if removes_admin:
        other_admins = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == p.org_id,
                User.role == Role.ADMIN.value,
                User.is_active.is_(True),
                User.id != user.id,
            )
        )
        if not other_admins:
            raise conflict("last_admin", "An organization must keep at least one active admin")

    if new_active and not user.is_active:
        await ledger.lock(db, p.org_id)
        await seats.ensure_free_seat(db, p.org)
    changes: dict[str, object] = {}
    if body.full_name is not None and body.full_name.strip() != user.full_name:
        changes["full_name"] = body.full_name.strip()
    if new_role != user.role:
        changes["role"] = {"from": user.role, "to": new_role}
    if new_active != user.is_active:
        changes["is_active"] = new_active

    user.full_name = body.full_name.strip() if body.full_name else user.full_name
    user.role = new_role
    user.is_active = new_active
    if not new_active:
        await svc.revoke_user_sessions(db, user.id, "user_deactivated")
    if changes:
        record(db, org_id=p.org_id, user_id=p.user.id, action="user.updated", ip=p.ip,
               meta={"target": str(user.id), "changes": changes})
    await db.commit()
    return user


# --- Invitations -------------------------------------------------------------
@router.get("/invitations", response_model=list[InviteOut])
async def list_invitations(
    p: Principal = Depends(require_permission(Permission.INVITATIONS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(Invitation)
        .where(
            Invitation.organization_id == p.org_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .order_by(Invitation.created_at.desc())
    )
    return list(rows)


@router.post("/invitations", response_model=InviteOut, status_code=201)
async def create_invitation(
    body: InviteIn,
    background: BackgroundTasks,
    p: Principal = Depends(require_permission(Permission.INVITATIONS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    if p.org.account_type == AccountType.SOLO_BUYER:
        raise ApiError(409, "solo_account", "Solo buyer accounts have a single member and cannot invite others")
    await ledger.lock(db, p.org_id)  # serialize seat checks for this account
    await seats.ensure_free_seat(db, p.org)
    email = svc.normalize_email(body.email)
    if await svc.email_in_use(db, email, p.org_id):
        raise ApiError(409, "email_unavailable", "This email address cannot be invited")
    raw = sec.new_opaque_token()
    invite = Invitation(
        organization_id=p.org_id,
        email=email,
        role=body.role.value,
        token_hash=sec.sha256_hex(raw),
        invited_by=p.user.id,
        expires_at=svc.utcnow() + timedelta(days=settings.invite_token_days),
    )
    db.add(invite)
    org = await db.get(Organization, p.org_id)
    record(db, org_id=p.org_id, user_id=p.user.id, action="invitation.created", ip=p.ip,
           meta={"email": email, "role": body.role.value})
    await db.flush()
    await db.refresh(invite)
    await db.commit()
    subject, text = mail.invitation_email(org.name if org else "your organization", body.role.value, raw)
    background.add_task(mail.send_email, email, subject, text)
    return invite


@router.delete("/invitations/{invitation_id}", response_model=MessageOut)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    p: Principal = Depends(require_permission(Permission.INVITATIONS_MANAGE)),
    db: AsyncSession = Depends(get_db),
):
    invite = await db.scalar(
        select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.organization_id == p.org_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    )
    if invite is None:
        raise not_found("Invitation not found")
    invite.revoked_at = svc.utcnow()
    record(db, org_id=p.org_id, user_id=p.user.id, action="invitation.revoked", ip=p.ip,
           meta={"email": invite.email})
    await db.commit()
    return MessageOut(message="Invitation revoked")


# --- Audit log ---------------------------------------------------------------
@router.get("/audit-logs", response_model=list[AuditOut])
async def list_audit_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None, max_length=80),
    p: Principal = Depends(require_permission(Permission.AUDIT_READ)),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(AuditLog, User.email)
        .join(User, User.id == AuditLog.user_id, isouter=True)
        .where(AuditLog.organization_id == p.org_id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action))
    rows = (await db.execute(stmt)).all()
    return [
        AuditOut(
            id=a.id, user_id=a.user_id, actor_email=email, action=a.action, ip=a.ip,
            meta=a.meta, created_at=a.created_at,
        )
        for a, email in rows
    ]
