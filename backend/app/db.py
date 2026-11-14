import uuid
from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
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


_TENANT_SQL = text("SELECT set_config('app.current_org', :org, true), set_config('app.system', 'off', true)")


@event.listens_for(Session, "after_begin")
def _reapply_tenant(session, _transaction, connection) -> None:
    """The tenant scope is transaction-local in PostgreSQL. A session scoped with set_tenant keeps that scope in
    every later transaction too (after a commit or rollback), so reads after a commit cannot silently see nothing."""
    org = session.info.get("tenant")
    if org is not None:
        connection.execute(_TENANT_SQL, {"org": str(org)})


async def set_tenant(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Scope the session to a single organization (row-level security), in this and later transactions."""
    session.info["tenant"] = org_id
    await session.execute(
        text("SELECT set_config('app.current_org', :org, true), set_config('app.system', 'off', true)"),
        {"org": str(org_id)},
    )


async def set_system(session: AsyncSession) -> None:
    """Pre-authentication lookups (login, token redemption). Never reachable from user input."""
    session.info.pop("tenant", None)
    await session.execute(
        text("SELECT set_config('app.system', 'on', true), set_config('app.current_org', '', true)")
    )
