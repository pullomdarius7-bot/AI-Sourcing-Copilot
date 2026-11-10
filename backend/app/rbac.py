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
    REQUIREMENTS_READ = "requirements:read"
    REQUIREMENTS_EXTRACT = "requirements:extract"
    REQUIREMENTS_REVIEW = "requirements:review"
    REQUIREMENTS_APPROVE = "requirements:approve"
    SUPPLIERS_READ = "suppliers:read"
    SUPPLIERS_MANAGE = "suppliers:manage"
    SUPPLIERS_QUALIFY = "suppliers:qualify"
    RFQS_READ = "rfqs:read"
    RFQS_MANAGE = "rfqs:manage"
    RFQS_APPROVE = "rfqs:approve"
    QUOTES_MANAGE = "quotes:manage"
    COMPARISONS_READ = "comparisons:read"
    COMPARISONS_RUN = "comparisons:run"
    REFERENCE_MANAGE = "reference:manage"  # exchange rates and unit conversion rules


_BASE = {
    Permission.ORG_READ, Permission.DOCUMENTS_READ, Permission.REQUIREMENTS_READ,
    Permission.SUPPLIERS_READ, Permission.RFQS_READ, Permission.COMPARISONS_READ,
}
_UPLOAD = {Permission.DOCUMENTS_UPLOAD, Permission.REQUIREMENTS_EXTRACT}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.REQUESTER: frozenset(_BASE | _UPLOAD),
    Role.BUYER: frozenset(
        _BASE | _UPLOAD | {
            Permission.DOCUMENTS_DELETE, Permission.REQUIREMENTS_REVIEW, Permission.SUPPLIERS_MANAGE,
            Permission.RFQS_MANAGE, Permission.QUOTES_MANAGE, Permission.COMPARISONS_RUN,
        }
    ),
    Role.APPROVER: frozenset(
        _BASE | {
            Permission.REQUIREMENTS_REVIEW, Permission.REQUIREMENTS_APPROVE, Permission.SUPPLIERS_QUALIFY,
            Permission.RFQS_APPROVE, Permission.COMPARISONS_RUN, Permission.REFERENCE_MANAGE,
        }
    ),
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
