"""Who may approve what. Approvals need a second person (separation of duties) only when there is one: an
account with a single active user (every solo account) approves its own work."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User

settings = get_settings()


async def second_person_required(db: AsyncSession, org_id: uuid.UUID) -> bool:
    if not settings.require_separate_approver:
        return False
    active = await db.scalar(select(func.count()).select_from(User).where(
        User.organization_id == org_id, User.is_active.is_(True)))
    return (active or 0) > 1
