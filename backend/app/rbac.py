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
    DOCUMENTS_READ = "documents:read"
    DOCUMENTS_UPLOAD = "documents:upload"
    DOCUMENTS_DELETE = "documents:delete"
    JOBS_READ = "jobs:read"
    JOBS_MANAGE = "jobs:manage"


_BASE = {Permission.ORG_READ, Permission.DOCUMENTS_READ}
_UPLOAD = {Permission.DOCUMENTS_UPLOAD}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.REQUESTER: frozenset(_BASE | _UPLOAD),
    Role.BUYER: frozenset(_BASE | _UPLOAD | {Permission.DOCUMENTS_DELETE}),
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
