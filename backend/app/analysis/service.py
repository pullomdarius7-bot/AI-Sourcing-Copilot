"""Reference data for quote analysis: exchange rates and unit conversion rules recorded by the organization."""
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import schemas as asch
from app.analysis import units
from app.deps import Principal
from app.errors import bad_request, conflict, not_found
from app.events import emit_event
from app.models import FxRate, UnitConversion


def _event(db: AsyncSession, p: Principal, entity: str, entity_id: uuid.UUID, action: str, data: dict[str, Any]) -> None:
    emit_event(db, org_id=p.org_id, entity_type=entity, entity_id=entity_id, action=action, actor=p.user.id,
               data=data, ip=p.ip)


# --- Exchange rates -------------------------------------------------------------------------------------------------------
async def list_rates(db: AsyncSession, org_id: uuid.UUID, currency: str | None = None) -> list[FxRate]:
    stmt = select(FxRate).where(FxRate.organization_id == org_id)
    if currency:
        c = currency.upper()
        stmt = stmt.where((FxRate.base == c) | (FxRate.quote == c))
    return list(await db.scalars(stmt.order_by(FxRate.base, FxRate.quote, FxRate.valid_from.desc())))


async def create_rate(db: AsyncSession, p: Principal, body: asch.FxRateIn) -> FxRate:
    if body.base == body.quote:
        raise bad_request("invalid_pair", "Base and quote currency must differ")
    row = FxRate(organization_id=p.org_id, base=body.base, quote=body.quote, rate=body.rate, valid_from=body.valid_from,
                 source=body.source, created_by=p.user.id)
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        raise conflict("rate_exists", f"A {body.base}/{body.quote} rate for {body.valid_from} already exists; delete it first") from None
    _event(db, p, "fx_rate", row.id, "created", {"pair": f"{body.base}/{body.quote}", "rate": str(body.rate),
                                                 "valid_from": body.valid_from.isoformat(), "source": body.source})
    await db.refresh(row)
    await db.commit()
    return row


async def delete_rate(db: AsyncSession, p: Principal, rate_id: uuid.UUID) -> None:
    row = await db.scalar(select(FxRate).where(FxRate.id == rate_id, FxRate.organization_id == p.org_id))
    if row is None:
        raise not_found("Exchange rate not found")
    _event(db, p, "fx_rate", row.id, "deleted", {"pair": f"{row.base}/{row.quote}", "rate": str(row.rate),
                                                 "valid_from": row.valid_from.isoformat()})
    await db.delete(row)
    await db.commit()


def rates_as_inputs(rows: list[FxRate]) -> list[dict[str, Any]]:
    return [{"base": r.base, "quote": r.quote, "rate": str(r.rate.normalize()), "valid_from": r.valid_from.isoformat()}
            for r in sorted(rows, key=lambda r: (r.base, r.quote, r.valid_from))]


# --- Unit conversions -----------------------------------------------------------------------------------------------------
async def list_conversions(db: AsyncSession, org_id: uuid.UUID) -> list[UnitConversion]:
    return list(await db.scalars(select(UnitConversion).where(UnitConversion.organization_id == org_id)
                                 .order_by(UnitConversion.from_unit, UnitConversion.to_unit)))


async def create_conversion(db: AsyncSession, p: Principal, body: asch.UnitConversionIn) -> UnitConversion:
    if body.from_unit == body.to_unit:
        raise bad_request("invalid_units", "From and to units must differ")
    if units.dimension(body.from_unit) and units.dimension(body.from_unit) == units.dimension(body.to_unit):
        raise bad_request("standard_conversion", f"{body.from_unit} to {body.to_unit} is a standard conversion and needs no rule")
    existing = await db.scalar(select(UnitConversion).where(
        UnitConversion.organization_id == p.org_id,
        ((UnitConversion.from_unit == body.from_unit) & (UnitConversion.to_unit == body.to_unit))
        | ((UnitConversion.from_unit == body.to_unit) & (UnitConversion.to_unit == body.from_unit)),
    ))
    if existing is not None:
        raise conflict("rule_exists", f"A rule between {body.from_unit} and {body.to_unit} already exists; delete it first")
    row = UnitConversion(organization_id=p.org_id, from_unit=body.from_unit, to_unit=body.to_unit, factor=body.factor,
                         note=body.note, created_by=p.user.id)
    db.add(row)
    await db.flush()
    _event(db, p, "unit_rule", row.id, "created", {"rule": f"1 {body.from_unit} = {body.factor} {body.to_unit}", "note": body.note})
    await db.refresh(row)
    await db.commit()
    return row


async def delete_conversion(db: AsyncSession, p: Principal, rule_id: uuid.UUID) -> None:
    row = await db.scalar(select(UnitConversion).where(UnitConversion.id == rule_id, UnitConversion.organization_id == p.org_id))
    if row is None:
        raise not_found("Unit rule not found")
    _event(db, p, "unit_rule", row.id, "deleted", {"rule": f"1 {row.from_unit} = {row.factor} {row.to_unit}"})
    await db.delete(row)
    await db.commit()


def conversions_as_inputs(rows: list[UnitConversion]) -> list[dict[str, Any]]:
    return [{"from_unit": r.from_unit, "to_unit": r.to_unit, "factor": str(r.factor.normalize())}
            for r in sorted(rows, key=lambda r: (r.from_unit, r.to_unit))]


def unit_catalogue() -> list[asch.UnitInfo]:
    by_unit: dict[str, list[str]] = {}
    for alias, target in units.ALIASES.items():
        by_unit.setdefault(target, []).append(alias)
    return [asch.UnitInfo(unit=u, dimension=dim, to_base=format(f.normalize(), "f"), aliases=sorted(by_unit.get(u, [])))
            for dim, table in units.DIMENSIONS.items() for u, f in table.items()]


def check_conversion(from_unit: str, to_unit: str, rules: list[dict[str, Any]]) -> asch.ConvertCheckOut:
    conv = units.factor(from_unit, to_unit, rules)
    return asch.ConvertCheckOut(
        from_unit=units.canonical(from_unit) or from_unit, to_unit=units.canonical(to_unit) or to_unit,
        factor=format(conv.factor.normalize(), "f") if conv else None, path=conv.path if conv else None,
        custom=conv.custom if conv else False,
    )

