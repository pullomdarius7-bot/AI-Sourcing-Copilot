"""Read-only export API for the Sourcing Team plan: deep analyses, sourcing results and the team's own manifests,
for spreadsheets, BI tools and internal systems.

Keys are created in the app (shown once), stored only as a SHA-256 hash, and can be revoked at any time. A key sees
exactly what its account sees, and nothing can be changed through it.

    curl -H "Authorization: Bearer sca_..." https://<host>/api/export/analyses
"""
import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record
from app.billing.plans import team_features
from app.db import get_db, set_system, set_tenant
from app.deps import Principal, require_permission
from app.errors import forbidden, not_found, unauthorized
from app.marketplace.service import items_of
from app.models import ApiKey, LotAnalysis, MarketplaceLot, Organization, SourcingCandidate, SourcingRequest
from app.rbac import Permission

router = APIRouter(tags=["export"])
PREFIX = "sca_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def _key_out(k: ApiKey) -> dict[str, Any]:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "created_at": k.created_at, "last_used_at": k.last_used_at,
            "revoked_at": k.revoked_at}


def _require_team(p: Principal) -> None:
    if not team_features(p.org):
        raise forbidden("The export API is part of the Sourcing Team plan", "team_plan_required")


@router.get("/api-keys")
async def list_keys(p: Principal = Depends(require_permission(Permission.ORG_MANAGE)), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(ApiKey).where(ApiKey.organization_id == p.org_id).order_by(ApiKey.created_at.desc()))
    return {"available": team_features(p.org), "keys": [_key_out(k) for k in rows]}


@router.post("/api-keys", status_code=201)
async def create_key(body: KeyIn, p: Principal = Depends(require_permission(Permission.ORG_MANAGE)),
                     db: AsyncSession = Depends(get_db)):
    _require_team(p)
    raw = PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(organization_id=p.org_id, name=body.name.strip(), prefix=raw[:12], key_hash=_hash(raw),
                 created_by=p.user.id)
    db.add(key)
    await db.flush()
    record(db, org_id=p.org_id, user_id=p.user.id, action="api_key.created", ip=p.ip,
           meta={"key_id": str(key.id), "name": key.name})
    await db.refresh(key)
    await db.commit()
    return {**_key_out(key), "key": raw}  # the only time the full key is shown


@router.delete("/api-keys/{key_id}")
async def revoke_key(key_id: uuid.UUID, p: Principal = Depends(require_permission(Permission.ORG_MANAGE)),
                     db: AsyncSession = Depends(get_db)):
    key = await db.get(ApiKey, key_id)
    if key is None or key.organization_id != p.org_id:
        raise not_found("Key not found")
    key.revoked_at = key.revoked_at or datetime.now(UTC)
    record(db, org_id=p.org_id, user_id=p.user.id, action="api_key.revoked", ip=p.ip, meta={"key_id": str(key.id)})
    await db.commit()
    return _key_out(key)


async def api_org(request: Request, db: AsyncSession = Depends(get_db)) -> Organization:
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not supplied.startswith(PREFIX):
        raise unauthorized("An export API key is required", "invalid_api_key")
    await set_system(db)
    key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == _hash(supplied), ApiKey.revoked_at.is_(None)))
    org = await db.get(Organization, key.organization_id) if key else None
    if key is None or org is None:
        raise unauthorized("The API key is not valid", "invalid_api_key")
    if not team_features(org):
        raise forbidden("The export API is part of the Sourcing Team plan", "team_plan_required")
    key.last_used_at = datetime.now(UTC)
    await db.commit()
    await set_tenant(db, org.id)
    return org


def _since(value: datetime | None) -> datetime:
    return value or datetime(1970, 1, 1, tzinfo=UTC)


@router.get("/export/analyses")
async def export_analyses(since: datetime | None = None, limit: int = Query(default=100, ge=1, le=500),
                          org: Organization = Depends(api_org), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(LotAnalysis).where(
        LotAnalysis.organization_id == org.id, LotAnalysis.status == "succeeded",
        LotAnalysis.completed_at > _since(since)).order_by(LotAnalysis.completed_at).limit(limit))).all()
    return {"data": [{"id": a.id, "lot_id": a.lot_id, "completed_at": a.completed_at, "params": a.params,
                      "method": a.method, "result": a.result} for a in rows],
            "next_since": rows[-1].completed_at if rows else None}


@router.get("/export/sourcing-requests")
async def export_requests(since: datetime | None = None, limit: int = Query(default=100, ge=1, le=500),
                          org: Organization = Depends(api_org), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(SourcingRequest).where(
        SourcingRequest.organization_id == org.id, SourcingRequest.created_at > _since(since)
    ).order_by(SourcingRequest.created_at).limit(limit))).all()
    out = []
    for r in rows:
        candidates = (await db.scalars(select(SourcingCandidate).where(SourcingCandidate.request_id == r.id))).all()
        out.append({
            **{k: getattr(r, k) for k in ("id", "title", "description", "category", "condition", "quantity",
                                          "target_unit_price", "region", "needed_by", "status", "web_status",
                                          "created_at")},
            "suppliers": [{k: getattr(c, k) for k in ("kind", "lot_id", "company_name", "website", "business_type",
                                                      "location", "summary", "categories", "products", "evidence",
                                                      "moq", "price_notes", "contact_email", "contact_phone",
                                                      "relevance", "status")} for c in candidates],
        })
    return {"data": out, "next_since": rows[-1].created_at if rows else None}


@router.get("/export/manifests")
async def export_manifests(org: Organization = Depends(api_org), db: AsyncSession = Depends(get_db)):
    lots = (await db.scalars(select(MarketplaceLot).where(
        MarketplaceLot.organization_id == org.id, MarketplaceLot.private.is_(True), MarketplaceLot.ingest_status == "ready",
    ).order_by(MarketplaceLot.created_at.desc()).limit(200))).all()
    out = []
    for lot in lots:
        out.append({
            "id": lot.id, "title": lot.title, "source_filename": lot.source_filename, "asking_price": lot.asking_price,
            "total_units": lot.total_units, "total_msrp": lot.total_msrp, "created_at": lot.created_at,
            "items": [{k: getattr(i, k) for k in ("title", "brand", "category", "condition", "upc", "asin",
                                                  "model_number", "quantity", "unit_msrp", "unit_price")}
                      for i in await items_of(db, lot.id)],
        })
    return {"data": out}
