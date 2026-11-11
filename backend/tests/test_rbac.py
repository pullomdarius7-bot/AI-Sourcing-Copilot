import uuid

import pytest

from app.rbac import ROLE_PERMISSIONS, Permission, Role, has_permission, permissions_for
from tests.helpers import add_member, register_admin

ROLES = [r.value for r in Role]


def test_permission_matrix():
    assert set(ROLE_PERMISSIONS[Role.ADMIN]) == set(Permission)
    everyone = {"org:read", "documents:read", "requirements:read", "suppliers:read", "rfqs:read", "comparisons:read"}
    for role in Role:
        assert everyone <= set(permissions_for(role.value)), role
    assert has_permission("auditor", Permission.AUDIT_READ) and has_permission("auditor", Permission.USERS_READ)
    for perm in (Permission.USERS_MANAGE, Permission.DOCUMENTS_UPLOAD, Permission.REQUIREMENTS_EXTRACT,
                 Permission.SUPPLIERS_MANAGE, Permission.RFQS_MANAGE, Permission.COMPARISONS_RUN):
        assert not has_permission("auditor", perm), perm
    assert {"documents:upload", "requirements:extract"} <= set(permissions_for("requester"))
    for perm in (Permission.REQUIREMENTS_REVIEW, Permission.SUPPLIERS_MANAGE, Permission.RFQS_MANAGE, Permission.QUOTES_MANAGE):
        assert not has_permission("requester", perm), perm
    assert {"suppliers:manage", "rfqs:manage", "quotes:manage", "comparisons:run", "documents:delete"} <= set(permissions_for("buyer"))
    for perm in (Permission.REQUIREMENTS_APPROVE, Permission.RFQS_APPROVE, Permission.SUPPLIERS_QUALIFY, Permission.JOBS_READ):
        assert not has_permission("buyer", perm), perm
    assert {"requirements:approve", "suppliers:qualify", "rfqs:approve", "comparisons:run"} <= set(permissions_for("approver"))
    assert not has_permission("approver", Permission.RFQS_MANAGE) and not has_permission("approver", Permission.QUOTES_MANAGE)
    assert has_permission("approver", Permission.REFERENCE_MANAGE) and has_permission("admin", Permission.REFERENCE_MANAGE)
    for role in ("buyer", "requester", "auditor"):
        assert not has_permission(role, Permission.REFERENCE_MANAGE), role
    assert has_permission("admin", Permission.JOBS_MANAGE)
    assert not has_permission("nonsense", Permission.ORG_READ)


@pytest.mark.parametrize("role", ROLES)
async def test_endpoint_access_matches_role(client, role):
    admin = await register_admin(client)
    member = admin if role == "admin" else await add_member(client, admin, f"{role}@acme.test", role)
    h = member.headers

    assert (await client.get("/organization", headers=h)).status_code == 200
    assert (await client.get("/auth/me", headers=h)).status_code == 200

    can_read_users = role in ("admin", "auditor")
    assert (await client.get("/users", headers=h)).status_code == (200 if can_read_users else 403)
    assert (await client.get("/audit-logs", headers=h)).status_code == (200 if can_read_users else 403)

    is_admin = role == "admin"
    assert (await client.get("/invitations", headers=h)).status_code == (200 if is_admin else 403)
    invite = await client.post("/invitations", json={"email": f"x-{role}@acme.test", "role": "buyer"}, headers=h)
    assert invite.status_code == (201 if is_admin else 403)
    assert (await client.patch("/organization", json={"name": "Renamed Co"}, headers=h)).status_code == (
        200 if is_admin else 403
    )
    assert (await client.patch(f"/users/{member.user_id}", json={"full_name": "Same Person"}, headers=h)).status_code == (
        200 if is_admin else 403
    )


async def test_unauthenticated_requests_are_rejected(client):
    for method, path in [("GET", "/users"), ("GET", "/organization"), ("GET", "/audit-logs"), ("POST", "/invitations")]:
        res = await client.request(method, path)
        assert res.status_code == 401, path


async def test_last_admin_cannot_be_demoted_or_deactivated(client):
    admin = await register_admin(client)
    demote = await client.patch(f"/users/{admin.user_id}", json={"role": "buyer"}, headers=admin.headers)
    assert demote.status_code == 409 and demote.json()["error"]["code"] == "last_admin"
    deactivate = await client.patch(f"/users/{admin.user_id}", json={"is_active": False}, headers=admin.headers)
    assert deactivate.status_code == 409


async def test_admin_can_change_role_and_it_takes_effect_immediately(client):
    admin = await register_admin(client)
    buyer = await add_member(client, admin, "buyer@acme.test", "buyer")
    assert (await client.get("/users", headers=buyer.headers)).status_code == 403
    res = await client.patch(f"/users/{buyer.user_id}", json={"role": "auditor"}, headers=admin.headers)
    assert res.status_code == 200 and res.json()["role"] == "auditor"
    assert (await client.get("/users", headers=buyer.headers)).status_code == 200


async def test_second_admin_allows_demoting_first(client):
    admin = await register_admin(client)
    second = await add_member(client, admin, "second@acme.test", "admin")
    res = await client.patch(f"/users/{admin.user_id}", json={"role": "buyer"}, headers=second.headers)
    assert res.status_code == 200


async def test_deactivated_user_is_locked_out_immediately(client):
    admin = await register_admin(client)
    buyer = await add_member(client, admin, "buyer@acme.test", "buyer")
    res = await client.patch(f"/users/{buyer.user_id}", json={"is_active": False}, headers=admin.headers)
    assert res.status_code == 200
    assert (await client.get("/auth/me", headers=buyer.headers)).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": buyer.refresh})).status_code == 401
    login = await client.post("/auth/login", json={"email": buyer.email, "password": buyer.password})
    assert login.status_code == 403 and login.json()["error"]["code"] == "account_disabled"


async def test_invalid_role_rejected(client):
    admin = await register_admin(client)
    res = await client.post("/invitations", json={"email": "x@acme.test", "role": "superuser"}, headers=admin.headers)
    assert res.status_code == 422
    res = await client.patch(f"/users/{admin.user_id}", json={"role": "root"}, headers=admin.headers)
    assert res.status_code == 422


async def test_invitation_lifecycle(client):
    from tests.helpers import PASSWORD, token_from_email

    admin = await register_admin(client)
    created = await client.post("/invitations", json={"email": "New@Acme.test", "role": "approver"}, headers=admin.headers)
    assert created.status_code == 201 and created.json()["email"] == "new@acme.test"
    token = token_from_email("new@acme.test")

    listed = (await client.get("/invitations", headers=admin.headers)).json()
    assert len(listed) == 1

    accept = await client.post(
        "/auth/accept-invitation", json={"token": token, "full_name": "Nina", "password": PASSWORD}
    )
    assert accept.status_code == 201 and accept.json()["role"] == "approver"
    assert accept.json()["email_verified"] is True
    reuse = await client.post(
        "/auth/accept-invitation", json={"token": token, "full_name": "Nina", "password": PASSWORD}
    )
    assert reuse.status_code == 400
    assert (await client.get("/invitations", headers=admin.headers)).json() == []


async def test_revoked_invitation_cannot_be_accepted(client):
    from tests.helpers import PASSWORD, token_from_email

    admin = await register_admin(client)
    created = (await client.post("/invitations", json={"email": "r@acme.test", "role": "buyer"}, headers=admin.headers)).json()
    assert (await client.delete(f"/invitations/{created['id']}", headers=admin.headers)).status_code == 200
    res = await client.post(
        "/auth/accept-invitation",
        json={"token": token_from_email("r@acme.test"), "full_name": "R", "password": PASSWORD},
    )
    assert res.status_code == 400
    assert (await client.delete(f"/invitations/{uuid.uuid4()}", headers=admin.headers)).status_code == 404


async def test_cannot_invite_existing_user(client):
    admin = await register_admin(client)
    res = await client.post("/invitations", json={"email": admin.email, "role": "buyer"}, headers=admin.headers)
    assert res.status_code == 409
