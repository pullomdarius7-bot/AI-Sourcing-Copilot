import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record
from app.models import DomainEvent


def emit_event(
    db: AsyncSession, *, org_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID, action: str,
    actor: uuid.UUID | None, from_state: str | None = None, to_state: str | None = None,
    data: dict[str, Any] | None = None, note: str | None = None, ip: str | None = None,
) -> None:
    """Append to the entity's history and to the organization audit log in the caller's transaction."""
    db.add(
        DomainEvent(
            organization_id=org_id, entity_type=entity_type, entity_id=entity_id, action=action,
            actor_id=actor, from_state=from_state, to_state=to_state, data=data, note=note,
        )
    )
    meta: dict[str, Any] = {"entity_id": str(entity_id), "from": from_state, "to": to_state}
    if note:
        meta["note"] = note[:300]
    record(db, org_id=org_id, user_id=actor, action=f"{entity_type}.{action}", ip=ip, meta=meta)


async def next_number(db: AsyncSession, org_id: uuid.UUID, name: str) -> int:
    """Gap-free per-organization sequence (row-locked upsert inside the caller's transaction)."""
    result = await db.execute(
        text(
            "INSERT INTO counters (organization_id, name, value) VALUES (CAST(:o AS uuid), :n, 1) "
            "ON CONFLICT (organization_id, name) DO UPDATE SET value = counters.value + 1 RETURNING value"
        ),
        {"o": str(org_id), "n": name},
    )
    return int(result.scalar_one())
