import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, set_tenant
from app.errors import forbidden, unauthorized
from app.models import AuthSession, Organization, User
from app.rbac import Permission, has_permission
from app.security import TokenError, decode_token

_bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user: User
    session: AuthSession
    org: Organization
    ip: str | None

    @property
    def org_id(self) -> uuid.UUID:
        return self.user.organization_id


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    if creds is None:
        raise unauthorized()
    try:
        claims = decode_token(creds.credentials, "access")
        user_id = uuid.UUID(claims["sub"])
        org_id = uuid.UUID(claims["org"])
        sid = uuid.UUID(claims["sid"])
    except (TokenError, KeyError, ValueError):
        raise unauthorized("Invalid or expired token", "invalid_token") from None

    await set_tenant(db, org_id)  # all further reads are confined to this organization
    user = await db.get(User, user_id)
    session = await db.get(AuthSession, sid)
    org = await db.get(Organization, org_id)
    now = datetime.now(UTC)
    if (
        user is None
        or session is None
        or org is None
        or user.organization_id != org_id
        or not user.is_active
        or session.user_id != user.id
        or session.revoked_at is not None
        or session.expires_at <= now
    ):
        raise unauthorized("Session is no longer valid", "invalid_session")
    return Principal(user=user, session=session, org=org, ip=client_ip(request))


def require_permission(*permissions: Permission) -> Callable[..., Principal]:
    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        # No product feature is reachable until the account has answered its intake form.
        if not principal.org.onboarding_completed:
            raise forbidden("Complete your account profile to continue", "onboarding_required")
        if not all(has_permission(principal.user.role, p) for p in permissions):
            raise forbidden()
        return principal

    return dependency
