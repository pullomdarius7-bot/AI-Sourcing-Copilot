"""The back office: the platform owner's view of every account, plus health checks and manual fixes.

Only users marked is_platform_admin (set from the command line, never through the API) can reach it; everyone else
gets 404, so the back office does not reveal it exists. Queries run in system context (across all tenants). Every
change is written to platform_audit with the admin's identity and the reason given.
"""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing import ledger
from app.config import get_settings
from app.db import get_db, set_system
from app.deps import Principal, get_principal
from app.errors import bad_request, conflict, forbidden, not_found
from app.jobs import queue
from app.models import (
    CreditTransaction,
    Document,
    EmailSuppression,
    Job,
    Organization,
    PlatformAudit,
    SourcingRequest,
    SupplierInvite,
    User,
)
from app.platform.health import run_checks
from app.services.auth import revoke_user_sessions
from app.sourcing import invites

settings = get_settings()
router = APIRouter(prefix="/platform", tags=["platform"])


def _now() -> datetime:
    return datetime.now(UTC)


async def require_platform_admin(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> Principal:
    if not p.user.is_platform_admin:
        raise not_found("Not found")
    if settings.platform_admin_requires_mfa and not p.user.mfa_enabled:
        raise forbidden("Turn on two-factor sign-in (Settings → Security) to use the back office", "mfa_required")
    await set_system(db)
    return p


def _audit(db: AsyncSession, p: Principal, action: str, target_type: str, target_id: Any, **meta: Any) -> None:
    db.add(PlatformAudit(actor_user_id=p.user.id, actor_email=p.user.email, action=action, target_type=target_type,
                         target_id=str(target_id), meta=meta, ip=p.ip))


class Reason(BaseModel):
    note: str = Field(min_length=3, max_length=300)


# --- Overview and health --------------------------------------------------------------------------------------------
@router.get("/overview")
async def overview(_: Principal = Depends(require_platform_admin), db: AsyncSession = Depends(get_db)):
    async def count(model, *where) -> int:
        return await db.scalar(select(func.count()).select_from(model).where(*where)) or 0

    week = _now() - timedelta(days=7)
    by_type = dict((await db.execute(select(Organization.account_type, func.count()).group_by(Organization.account_type))).all())
    by_sub = dict((await db.execute(select(Organization.subscription_status, func.count())
                                    .group_by(Organization.subscription_status))).all())
    by_job = dict((await db.execute(select(Job.status, func.count()).group_by(Job.status))).all())
    by_invite = dict((await db.execute(select(SupplierInvite.status, func.count()).group_by(SupplierInvite.status))).all())
    return {
        "organizations": sum(by_type.values()), "organizations_by_type": by_type, "subscriptions": by_sub,
        "users": await count(User), "active_users": await count(User, User.is_active.is_(True)),
        "signups_7d": await count(Organization, Organization.created_at > week),
        "trials_active": await count(Organization, Organization.trial_ends_at > _now(), Organization.plan.is_(None)),
        "sourcing_requests": await count(SourcingRequest),
        "sourcing_requests_7d": await count(SourcingRequest, SourcingRequest.created_at > week),
        "documents": await count(Document),
        "credits_spent_7d": -(await db.scalar(select(func.coalesce(func.sum(CreditTransaction.delta), 0)).where(
            CreditTransaction.delta < 0, CreditTransaction.state == "settled", CreditTransaction.created_at > week)) or 0),
        "jobs": by_job, "invites": by_invite,
    }


@router.get("/health")
async def health(_: Principal = Depends(require_platform_admin), db: AsyncSession = Depends(get_db)):
    checks = await run_checks(db)
    worst = "fail" if any(c["status"] == "fail" for c in checks) else "warn" if any(
        c["status"] == "warn" for c in checks) else "ok"
    return {"status": worst, "checked_at": _now(), "checks": checks}


# --- Organizations and users ----------------------------------------------------------------------------------------
def _org_out(o: Organization, users: int | None = None) -> dict[str, Any]:
    return {k: getattr(o, k) for k in (
        "id", "name", "account_type", "plan", "subscription_status", "credit_balance", "purchased_credits",
        "trial_ends_at", "current_period_end", "onboarding_completed", "signup_promo", "stripe_customer_id",
        "created_at")} | ({"users": users} if users is not None else {})


@router.get("/organizations")
async def organizations(q: str | None = Query(None, max_length=200), limit: int = Query(50, ge=1, le=200),
                        offset: int = Query(0, ge=0),
                        _: Principal = Depends(require_platform_admin), db: AsyncSession = Depends(get_db)):
    users = select(User.organization_id, func.count().label("n")).group_by(User.organization_id).subquery()
    stmt = select(Organization, func.coalesce(users.c.n, 0)).outerjoin(users, users.c.organization_id == Organization.id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        members = select(User.organization_id).where(User.email.ilike(like))
        stmt = stmt.where(or_(Organization.name.ilike(like), Organization.id.in_(members),
                              Organization.stripe_customer_id == q.strip()))
    rows = (await db.execute(stmt.order_by(Organization.created_at.desc()).limit(limit).offset(offset))).all()
    return [_org_out(o, n) for o, n in rows]


@router.get("/organizations/{org_id}")
async def organization(org_id: uuid.UUID, _: Principal = Depends(require_platform_admin),
                       db: AsyncSession = Depends(get_db)):
    org = await db.get(Organization, org_id)
    if org is None:
        raise not_found("Organization not found")
    users = (await db.scalars(select(User).where(User.organization_id == org_id).order_by(User.created_at))).all()
    credits = (await db.scalars(select(CreditTransaction).where(CreditTransaction.organization_id == org_id)
                                .order_by(CreditTransaction.id.desc()).limit(50))).all()
    return {
        **_org_out(org, len(users)), "onboarding_profile": org.onboarding_profile,
        "users": [{k: getattr(u, k) for k in ("id", "email", "full_name", "role", "is_active", "is_platform_admin",
                                               "mfa_enabled", "email_verified_at", "last_login_at", "created_at")}
                  for u in users],
        "credit_transactions": [{k: getattr(t, k) for k in ("id", "kind", "delta", "balance_after", "state", "note",
                                                            "action", "created_at")} for t in credits],
    }


class CreditAdjust(Reason):
    delta: int = Field(ge=-100_000, le=100_000)


@router.post("/organizations/{org_id}/credits")
async def adjust_credits(org_id: uuid.UUID, body: CreditAdjust, p: Principal = Depends(require_platform_admin),
                         db: AsyncSession = Depends(get_db)):
    if body.delta == 0:
        raise bad_request("invalid_delta", "Enter a non-zero number of credits")
    try:
        entry = await ledger.post(db, org_id, body.delta, "adjustment", note=f"Platform: {body.note}"[:300])
    except ledger.InsufficientCredits:
        raise conflict("insufficient_credits", "That would take the balance below zero") from None
    _audit(db, p, "credits.adjusted", "organization", org_id, delta=body.delta, note=body.note,
           balance_after=entry.balance_after)
    await db.commit()
    return {"balance": entry.balance_after}


class TrialExtend(Reason):
    days: int = Field(ge=1, le=90)


@router.post("/organizations/{org_id}/extend-trial")
async def extend_trial(org_id: uuid.UUID, body: TrialExtend, p: Principal = Depends(require_platform_admin),
                       db: AsyncSession = Depends(get_db)):
    org = await ledger.lock(db, org_id)
    if org.plan and org.subscription_status in ("active", "trialing", "past_due"):
        raise conflict("subscribed", "This account has a paid plan; its trial no longer applies")
    start = max(org.trial_ends_at or _now(), _now())
    org.trial_ends_at = start + timedelta(days=body.days)
    _audit(db, p, "trial.extended", "organization", org_id, days=body.days, note=body.note,
           trial_ends_at=org.trial_ends_at.isoformat())
    await db.commit()
    return {"trial_ends_at": org.trial_ends_at}


class UserActive(Reason):
    active: bool


@router.post("/users/{user_id}/active")
async def set_user_active(user_id: uuid.UUID, body: UserActive, p: Principal = Depends(require_platform_admin),
                          db: AsyncSession = Depends(get_db)):
    user = await db.get(User, user_id, with_for_update=True)
    if user is None:
        raise not_found("User not found")
    if user.id == p.user.id and not body.active:
        raise conflict("self", "You cannot deactivate your own account here")
    user.is_active = body.active
    if not body.active:
        await revoke_user_sessions(db, user.id, "platform_deactivated")
    _audit(db, p, "user.activated" if body.active else "user.deactivated", "user", user_id,
           email=user.email, note=body.note)
    await db.commit()
    return {"id": user.id, "is_active": user.is_active}


# --- Supplier invitations and opt-outs ------------------------------------------------------------------------------
def _invite_out(i: SupplierInvite) -> dict[str, Any]:
    return {k: getattr(i, k) for k in ("id", "domain", "email", "company_name", "source_url", "status", "status_note",
                                       "request_count", "sent_count", "last_sent_at", "joined_org_id", "created_at",
                                       "updated_at")}


@router.get("/invites")
async def list_invites(status: str | None = Query(None, max_length=12), q: str | None = Query(None, max_length=200),
                       limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                       _: Principal = Depends(require_platform_admin), db: AsyncSession = Depends(get_db)):
    stmt = select(SupplierInvite)
    if status:
        stmt = stmt.where(SupplierInvite.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(SupplierInvite.domain.ilike(like), SupplierInvite.company_name.ilike(like),
                              SupplierInvite.email.ilike(like)))
    rows = (await db.scalars(stmt.order_by(SupplierInvite.updated_at.desc()).limit(limit).offset(offset))).all()
    return [_invite_out(i) for i in rows]


class InviteSend(Reason):
    force: bool = False


@router.post("/invites/{invite_id}/send")
async def send_invite(invite_id: uuid.UUID, body: InviteSend, p: Principal = Depends(require_platform_admin),
                      db: AsyncSession = Depends(get_db)):
    invite = await db.get(SupplierInvite, invite_id, with_for_update=True)
    if invite is None:
        raise not_found("Invitation not found")
    outcome = await invites.deliver(db, invite, force=body.force)
    _audit(db, p, "invite.sent", "invite", invite_id, domain=invite.domain, force=body.force, note=body.note,
           outcome=outcome)
    await db.commit()
    return {**outcome, "invite": _invite_out(invite)}


@router.post("/invites/{invite_id}/hold")
async def hold_invite(invite_id: uuid.UUID, body: Reason, p: Principal = Depends(require_platform_admin),
                      db: AsyncSession = Depends(get_db)):
    invite = await db.get(SupplierInvite, invite_id, with_for_update=True)
    if invite is None:
        raise not_found("Invitation not found")
    if invite.status in ("suppressed", "joined"):
        raise conflict("final", f"This invitation is {invite.status}")
    invite.status, invite.status_note, invite.updated_at = "held", f"Held by the platform: {body.note}"[:300], _now()
    _audit(db, p, "invite.held", "invite", invite_id, domain=invite.domain, note=body.note)
    await db.commit()
    return _invite_out(invite)


@router.get("/suppressions")
async def suppressions(q: str | None = Query(None, max_length=320), limit: int = Query(100, ge=1, le=500),
                       _: Principal = Depends(require_platform_admin), db: AsyncSession = Depends(get_db)):
    stmt = select(EmailSuppression)
    if q and q.strip():
        stmt = stmt.where(EmailSuppression.value.ilike(f"%{q.strip()}%"))
    rows = (await db.scalars(stmt.order_by(EmailSuppression.created_at.desc()).limit(limit))).all()
    return [{"value": r.value, "reason": r.reason, "created_at": r.created_at} for r in rows]


class SuppressionLift(Reason):
    value: str = Field(min_length=3, max_length=320)


@router.post("/suppressions/lift")
async def lift_suppression(body: SuppressionLift, p: Principal = Depends(require_platform_admin),
                           db: AsyncSession = Depends(get_db)):
    """Only when the business itself asks to receive invitations again; the reason is kept in the audit log."""
    row = await db.get(EmailSuppression, body.value.lower())
    if row is None:
        raise not_found("That address or domain is not on the opt-out list")
    await db.delete(row)
    _audit(db, p, "suppression.lifted", "suppression", row.value, reason=row.reason, note=body.note)
    await db.commit()
    return {"lifted": row.value}


# --- Jobs -----------------------------------------------------------------------------------------------------------
@router.get("/jobs")
async def jobs(status: str | None = Query(None, pattern="^(queued|running|succeeded|dead)$"),
               kind: str | None = Query(None, max_length=60), limit: int = Query(50, ge=1, le=200),
               offset: int = Query(0, ge=0),
               _: Principal = Depends(require_platform_admin), db: AsyncSession = Depends(get_db)):
    stmt = select(Job, Organization.name).outerjoin(Organization, Organization.id == Job.organization_id)
    if status:
        stmt = stmt.where(Job.status == status)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    rows = (await db.execute(stmt.order_by(Job.created_at.desc()).limit(limit).offset(offset))).all()
    return [{**{k: getattr(j, k) for k in ("id", "organization_id", "kind", "status", "attempts", "max_attempts",
                                           "last_error", "run_at", "created_at", "finished_at")},
             "organization_name": name} for j, name in rows]


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: uuid.UUID, body: Reason, p: Principal = Depends(require_platform_admin),
                    db: AsyncSession = Depends(get_db)):
    from app.routers.jobs import _RESTORE_STATUS, ANALYZE

    job = await db.get(Job, job_id, with_for_update=True)
    if job is None:
        raise not_found("Job not found")
    if job.status != "dead":
        raise conflict("not_dead", "Only failed (dead) jobs can be retried")
    if job.dedupe_key and await db.scalar(select(Job.id).where(
            Job.dedupe_key == job.dedupe_key, Job.status.in_(("queued", "running")))):
        raise conflict("already_active", "An identical job is already queued")
    await queue.retry_dead(db, job)
    doc_id = job.payload.get("document_id")
    if doc_id and job.kind in _RESTORE_STATUS:
        doc = await db.get(Document, uuid.UUID(doc_id))
        if doc is not None and doc.status != "quarantined":
            doc.status, doc.error, doc.updated_at = _RESTORE_STATUS[job.kind], None, _now()
            if job.kind == ANALYZE:
                doc.analysis_status = "pending"
    _audit(db, p, "job.retried", "job", job_id, kind=job.kind, organization_id=str(job.organization_id), note=body.note)
    await db.commit()
    return {"id": job.id, "status": job.status}


# --- Audit ----------------------------------------------------------------------------------------------------------
@router.get("/audit")
async def audit(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                _: Principal = Depends(require_platform_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(PlatformAudit).order_by(PlatformAudit.id.desc()).limit(limit).offset(offset))).all()
    return [{k: getattr(r, k) for k in ("id", "actor_email", "action", "target_type", "target_id", "meta", "ip",
                                        "created_at")} for r in rows]

