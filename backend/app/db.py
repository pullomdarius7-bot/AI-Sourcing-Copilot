import uuid
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings

settings = get_settings()

_engine_kwargs: dict = {"pool_pre_ping": True}
if settings.environment == "test":
    _engine_kwargs["poolclass"] = NullPool
else:
    _engine_kwargs.update(pool_size=10, max_overflow=20)

engine = create_async_engine(settings.database_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """One session (and one transaction) per request. Services commit explicitly."""
    async with SessionLocal() as session:
        yield session


async def set_tenant(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Scope the current transaction to a single organization (row-level security)."""
    await session.execute(
        text("SELECT set_config('app.current_org', :org, true), set_config('app.system', 'off', true)"),
        {"org": str(org_id)},
    )


async def set_system(session: AsyncSession) -> None:
    """Pre-authentication lookups (login, token redemption). Never reachable from user input."""
    await session.execute(
        text("SELECT set_config('app.system', 'on', true), set_config('app.current_org', '', true)")
    )
