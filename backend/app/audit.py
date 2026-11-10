import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def record(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    ip: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    db.add(AuditLog(organization_id=org_id, user_id=user_id, action=action, ip=ip, meta=meta or {}))
