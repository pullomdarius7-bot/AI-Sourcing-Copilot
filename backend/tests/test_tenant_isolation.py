import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import security as sec
from app.db import SessionLocal, set_system, set_tenant
from tests.helpers import add_member, register_admin


async def two_orgs(client):
    a = await register_admin(client, email="admin@alpha.test", org="Alpha Ltd")
    b = await register_admin(client, email="admin@beta.test", org="Beta Ltd")
    return a, b


async def test_user_lists_are_scoped_to_own_org(client):
    a, b = await two_orgs(client)
    await add_member(client, a, "buyer@alpha.test", "buyer")
    emails_a = {u["email"] for u in (await client.get("/users", headers=a.headers)).json()}
    emails_b = {u["email"] for u in (await client.get("/users", headers=b.headers)).json()}
    assert emails_a == {"admin@alpha.test", "buyer@alpha.test"}
    assert emails_b == {"admin@beta.test"}


async def test_cannot_read_or_modify_other_orgs_users(client):
    a, b = await two_orgs(client)
    assert (await client.get(f"/users/{b.user_id}", headers=a.headers)).status_code == 404
    res = await client.patch(f"/users/{b.user_id}", json={"role": "buyer"}, headers=a.headers)
    assert res.status_code == 404
    res = await client.patch(f"/users/{b.user_id}", json={"is_active": False}, headers=a.headers)
    assert res.status_code == 404
    assert (await client.get("/auth/me", headers=b.headers)).status_code == 200


async def test_organization_endpoint_returns_only_own_org(client):
    a, b = await two_orgs(client)
    assert (await client.get("/organization", headers=a.headers)).json()["name"] == "Alpha Ltd"
    assert (await client.get("/organization", headers=b.headers)).json()["name"] == "Beta Ltd"


async def test_invitations_and_audit_logs_are_isolated(client):
    a, b = await two_orgs(client)
    created = (await client.post("/invitations", json={"email": "i@alpha.test", "role": "buyer"}, headers=a.headers)).json()
    assert (await client.get("/invitations", headers=b.headers)).json() == []
    assert (await client.delete(f"/invitations/{created['id']}", headers=b.headers)).status_code == 404
    logs_b = (await client.get("/audit-logs?limit=200", headers=b.headers)).json()
    assert logs_b and all(row["actor_email"] == "admin@beta.test" for row in logs_b)
    assert not any(row["action"].startswith("invitation.") for row in logs_b)


async def test_session_of_other_org_cannot_be_revoked(client):
    a, b = await two_orgs(client)
    sessions_b = (await client.get("/auth/sessions", headers=b.headers)).json()
    res = await client.delete(f"/auth/sessions/{sessions_b[0]['id']}", headers=a.headers)
    assert res.status_code == 404
    assert (await client.get("/auth/me", headers=b.headers)).status_code == 200


async def test_token_with_foreign_org_claim_is_rejected(client):
    a, b = await two_orgs(client)
    me_a = (await client.get("/auth/me", headers=a.headers)).json()
    sessions_a = (await client.get("/auth/sessions", headers=a.headers)).json()
    forged = sec.create_access_token(
        uuid.UUID(me_a["id"]), uuid.UUID(b.org_id), "admin", uuid.UUID(sessions_a[0]["id"])
    )
    res = await client.get("/users", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 401


async def test_same_email_cannot_exist_in_two_orgs(client):
    a, _ = await two_orgs(client)
    res = await client.post("/invitations", json={"email": "admin@beta.test", "role": "buyer"}, headers=a.headers)
    assert res.status_code == 409


# --- Database-level row-level security ------------------------------------------
async def test_app_role_is_not_superuser_and_cannot_bypass_rls():
    async with SessionLocal() as s:
        row = (await s.execute(text(
            "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ))).one()
    assert row.current_user == "copilot_app" and row.rolsuper is False and row.rolbypassrls is False


async def test_rls_hides_all_rows_without_tenant_context(client):
    await two_orgs(client)
    async with SessionLocal() as s:
        for table in ("users", "organizations", "auth_sessions", "audit_logs", "email_tokens"):
            assert await s.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table


async def test_rls_returns_only_current_tenant_rows(client):
    a, b = await two_orgs(client)
    async with SessionLocal() as s:
        await set_tenant(s, uuid.UUID(a.org_id))
        orgs = (await s.execute(text("SELECT id::text FROM organizations"))).scalars().all()
        users = (await s.execute(text("SELECT organization_id::text FROM users"))).scalars().all()
        assert orgs == [a.org_id] and set(users) == {a.org_id}
        await set_tenant(s, uuid.UUID(b.org_id))
        users = (await s.execute(text("SELECT organization_id::text FROM users"))).scalars().all()
        assert set(users) == {b.org_id}
        await set_system(s)
        assert await s.scalar(text("SELECT count(*) FROM users")) == 2


async def test_rls_blocks_writes_into_another_tenant(client):
    a, b = await two_orgs(client)
    async with SessionLocal() as s:
        await set_tenant(s, uuid.UUID(a.org_id))
        with pytest.raises(DBAPIError):
            await s.execute(
                text("INSERT INTO audit_logs (organization_id, action) VALUES (CAST(:o AS uuid), 'x')"), {"o": b.org_id}
            )
        await s.rollback()
        await set_tenant(s, uuid.UUID(a.org_id))
        updated = await s.execute(text("UPDATE users SET full_name = 'hacked' WHERE organization_id = CAST(:o AS uuid)"), {"o": b.org_id})
        assert updated.rowcount == 0


async def test_audit_log_is_append_only_for_app_role(client):
    a, _ = await two_orgs(client)
    async with SessionLocal() as s:
        await set_tenant(s, uuid.UUID(a.org_id))
        with pytest.raises(DBAPIError):
            await s.execute(text("UPDATE audit_logs SET action = 'tampered'"))
        await s.rollback()
        await set_tenant(s, uuid.UUID(a.org_id))
        with pytest.raises(DBAPIError):
            await s.execute(text("DELETE FROM audit_logs"))
