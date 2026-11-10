from enum import StrEnum


class Role(StrEnum):
    REQUESTER = "requester"
    BUYER = "buyer"
    APPROVER = "approver"
    ADMIN = "admin"
    AUDITOR = "auditor"


class Permission(StrEnum):
    ORG_READ = "org:read"
    ORG_MANAGE = "org:manage"
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    INVITATIONS_MANAGE = "invitations:manage"
    AUDIT_READ = "audit:read"


_BASE = {Permission.ORG_READ}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.REQUESTER: frozenset(_BASE),
    Role.BUYER: frozenset(_BASE),
    Role.APPROVER: frozenset(_BASE),
    Role.AUDITOR: frozenset(_BASE | {Permission.USERS_READ, Permission.AUDIT_READ}),
    Role.ADMIN: frozenset(Permission),
}


def permissions_for(role: str) -> list[str]:
    try:
        return sorted(p.value for p in ROLE_PERMISSIONS[Role(role)])
    except (ValueError, KeyError):
        return []


def has_permission(role: str, permission: Permission) -> bool:
    try:
        return permission in ROLE_PERMISSIONS[Role(role)]
    except (ValueError, KeyError):
        return False
