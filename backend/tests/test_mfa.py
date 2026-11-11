import uuid

from app import security as sec
from tests.helpers import PASSWORD, register_admin, totp_code


async def enable_mfa(client, admin):
    setup = await client.post("/auth/mfa/setup", headers=admin.headers)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    enable = await client.post("/auth/mfa/enable", json={"code": totp_code(secret)}, headers=admin.headers)
    assert enable.status_code == 200, enable.text
    return secret, enable.json()["recovery_codes"]


async def test_enable_mfa_returns_recovery_codes(client):
    admin = await register_admin(client)
    _, codes = await enable_mfa(client, admin)
    assert len(codes) == 10 and len(set(codes)) == 10
    assert (await client.get("/auth/me", headers=admin.headers)).json()["mfa_enabled"] is True


async def test_enable_rejects_wrong_code(client):
    admin = await register_admin(client)
    await client.post("/auth/mfa/setup", headers=admin.headers)
    res = await client.post("/auth/mfa/enable", json={"code": "000000"}, headers=admin.headers)
    assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_code"


async def test_enable_without_setup_rejected(client):
    admin = await register_admin(client)
    res = await client.post("/auth/mfa/enable", json={"code": "123456"}, headers=admin.headers)
    assert res.status_code == 400 and res.json()["error"]["code"] == "mfa_not_started"


async def test_secret_is_encrypted_at_rest(client, admin_engine):
    from sqlalchemy import text

    admin = await register_admin(client)
    secret, _ = await enable_mfa(client, admin)
    async with admin_engine.connect() as conn:
        stored = await conn.scalar(text("SELECT mfa_secret_enc FROM users"))
    assert secret not in stored and sec.decrypt_secret(stored) == secret


async def test_login_requires_second_factor(client):
    admin = await register_admin(client)
    secret, _ = await enable_mfa(client, admin)
    res = await client.post("/auth/login", json={"email": admin.email, "password": PASSWORD})
    body = res.json()
    assert res.status_code == 200 and body["mfa_required"] is True
    assert body["access_token"] is None and body["mfa_token"]

    done = await client.post("/auth/mfa/login", json={"mfa_token": body["mfa_token"], "code": totp_code(secret, 1)})
    assert done.status_code == 200
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {done.json()['access_token']}"})
    assert me.status_code == 200


async def test_totp_code_cannot_be_replayed(client):
    admin = await register_admin(client)
    secret, _ = await enable_mfa(client, admin)
    mfa_token = (await client.post("/auth/login", json={"email": admin.email, "password": PASSWORD})).json()["mfa_token"]
    code = totp_code(secret, 1)
    assert (await client.post("/auth/mfa/login", json={"mfa_token": mfa_token, "code": code})).status_code == 200
    replay = await client.post("/auth/mfa/login", json={"mfa_token": mfa_token, "code": code})
    assert replay.status_code == 401


async def test_wrong_totp_code_rejected_and_counts_toward_lockout(client):
    admin = await register_admin(client)
    await enable_mfa(client, admin)
    mfa_token = (await client.post("/auth/login", json={"email": admin.email, "password": PASSWORD})).json()["mfa_token"]
    for _ in range(5):
        res = await client.post("/auth/mfa/login", json={"mfa_token": mfa_token, "code": "000000"})
        assert res.status_code == 401
    locked = await client.post("/auth/mfa/login", json={"mfa_token": mfa_token, "code": "000000"})
    assert locked.status_code == 429


async def test_recovery_code_works_once(client):
    admin = await register_admin(client)
    _, codes = await enable_mfa(client, admin)
    for attempt, expected in ((1, 200), (2, 401)):
        mfa_token = (await client.post("/auth/login", json={"email": admin.email, "password": PASSWORD})).json()["mfa_token"]
        res = await client.post("/auth/mfa/login", json={"mfa_token": mfa_token, "code": codes[0]})
        assert res.status_code == expected, attempt


async def test_mfa_token_is_not_an_access_token(client):
    admin = await register_admin(client)
    await enable_mfa(client, admin)
    mfa_token = (await client.post("/auth/login", json={"email": admin.email, "password": PASSWORD})).json()["mfa_token"]
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {mfa_token}"})).status_code == 401


async def test_access_token_is_not_an_mfa_token(client):
    admin = await register_admin(client)
    secret, _ = await enable_mfa(client, admin)
    res = await client.post("/auth/mfa/login", json={"mfa_token": admin.access, "code": totp_code(secret, 1)})
    assert res.status_code == 401


async def test_disable_mfa_requires_password_and_code(client):
    admin = await register_admin(client)
    secret, codes = await enable_mfa(client, admin)
    bad_pw = await client.post(
        "/auth/mfa/disable", json={"password": "wrong-password-1", "code": totp_code(secret, 1)}, headers=admin.headers
    )
    assert bad_pw.status_code == 400
    ok = await client.post(
        "/auth/mfa/disable", json={"password": PASSWORD, "code": codes[1]}, headers=admin.headers
    )
    assert ok.status_code == 200
    login = await client.post("/auth/login", json={"email": admin.email, "password": PASSWORD})
    assert login.json()["mfa_required"] is False and login.json()["access_token"]


async def test_cannot_start_setup_twice_after_enable(client):
    admin = await register_admin(client)
    await enable_mfa(client, admin)
    res = await client.post("/auth/mfa/setup", headers=admin.headers)
    assert res.status_code == 409


async def test_mfa_token_for_unknown_user_rejected(client):
    token = sec.create_mfa_token(uuid.uuid4(), uuid.uuid4())
    res = await client.post("/auth/mfa/login", json={"mfa_token": token, "code": "123456"})
    assert res.status_code == 401
