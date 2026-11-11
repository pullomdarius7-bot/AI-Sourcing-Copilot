async def test_health(client):
    res = await client.get("/health")
    assert res.status_code == 200 and res.json() == {"status": "ok"}


async def test_ready_checks_database(client):
    res = await client.get("/ready")
    assert res.status_code == 200 and res.json() == {"status": "ready"}


async def test_error_envelope_for_unknown_route(client):
    res = await client.get("/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


async def test_validation_error_envelope(client):
    res = await client.post("/auth/login", json={"email": "not-an-email", "password": "x"})
    assert res.status_code == 422
    error = res.json()["error"]
    assert error["code"] == "validation_error" and error["details"][0]["field"] == "email"


async def test_security_headers(client):
    res = await client.get("/health")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["cache-control"] == "no-store"


async def test_protected_route_requires_bearer(client):
    res = await client.get("/auth/me")
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.json()["error"]["code"] == "unauthorized"


async def test_organization_update_and_audit(client):
    from tests.helpers import register_admin

    admin = await register_admin(client)
    res = await client.patch("/organization", json={"name": "Acme Global"}, headers=admin.headers)
    assert res.status_code == 200 and res.json()["name"] == "Acme Global"
    logs = await client.get("/audit-logs", headers=admin.headers)
    actions = [row["action"] for row in logs.json()]
    assert "organization.updated" in actions and "auth.register" in actions
    assert all(row["actor_email"] == admin.email for row in logs.json())


async def test_audit_log_filter_and_paging(client):
    from tests.helpers import register_admin

    admin = await register_admin(client)
    res = await client.get("/audit-logs?action=auth.&limit=1", headers=admin.headers)
    assert res.status_code == 200 and len(res.json()) == 1
    res = await client.get("/audit-logs?limit=0", headers=admin.headers)
    assert res.status_code == 422
