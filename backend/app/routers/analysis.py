"""Reference data for quote analysis: exchange rates and unit conversion rules."""
import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import schemas as asch
from app.analysis import service as svc
from app.db import get_db
from app.deps import Principal, require_permission
from app.rbac import Permission

router = APIRouter(tags=["analysis"])
READ, MANAGE = Permission.COMPARISONS_READ, Permission.REFERENCE_MANAGE


def _rate_out(r) -> asch.FxRateOut:
    return asch.FxRateOut(id=r.id, base=r.base, quote=r.quote, rate=r.rate, valid_from=r.valid_from, source=r.source,
                          created_by=r.created_by, created_at=r.created_at)


def _rule_out(r) -> asch.UnitConversionOut:
    return asch.UnitConversionOut(id=r.id, from_unit=r.from_unit, to_unit=r.to_unit, factor=r.factor, note=r.note,
                                  created_by=r.created_by, created_at=r.created_at)


@router.get("/fx-rates", response_model=list[asch.FxRateOut])
async def list_rates(currency: str | None = Query(default=None, pattern=r"^[A-Za-z]{3}$"),
                     p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    return [_rate_out(r) for r in await svc.list_rates(db, p.org_id, currency)]


@router.post("/fx-rates", response_model=asch.FxRateOut, status_code=201)
async def create_rate(body: asch.FxRateIn, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    return _rate_out(await svc.create_rate(db, p, body))


@router.delete("/fx-rates/{rate_id}", status_code=204)
async def delete_rate(rate_id: uuid.UUID, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    await svc.delete_rate(db, p, rate_id)
    return Response(status_code=204)


@router.get("/units", response_model=list[asch.UnitInfo])
async def unit_catalogue(p: Principal = Depends(require_permission(READ))):
    return svc.unit_catalogue()


@router.get("/unit-conversions", response_model=list[asch.UnitConversionOut])
async def list_conversions(p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    return [_rule_out(r) for r in await svc.list_conversions(db, p.org_id)]


@router.post("/unit-conversions", response_model=asch.UnitConversionOut, status_code=201)
async def create_conversion(body: asch.UnitConversionIn, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    return _rule_out(await svc.create_conversion(db, p, body))


@router.delete("/unit-conversions/{rule_id}", status_code=204)
async def delete_conversion(rule_id: uuid.UUID, p: Principal = Depends(require_permission(MANAGE)), db: AsyncSession = Depends(get_db)):
    await svc.delete_conversion(db, p, rule_id)
    return Response(status_code=204)


@router.get("/unit-conversions/check", response_model=asch.ConvertCheckOut)
async def check_conversion(from_unit: str = Query(min_length=1, max_length=30), to_unit: str = Query(min_length=1, max_length=30),
                           p: Principal = Depends(require_permission(READ)), db: AsyncSession = Depends(get_db)):
    rules = svc.conversions_as_inputs(await svc.list_conversions(db, p.org_id))
    return svc.check_conversion(from_unit, to_unit, rules)
