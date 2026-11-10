import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app import email as mail
from app import security as sec
from app.config import get_settings
from tests.helpers import PASSWORD, login, register_admin, token_from_email

settings = get_settings()


async def test_register_creates_org_admin_and_sends_verification(client):
    res = await client.post(
        "/auth/register",
        json={"organization_name": "Acme", "full_name": "Ada", "email": "Ada@Acme.test", "password": PASSWORD},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "ada@acme.test" and body["role"] == "admin"
    assert body["email_verified"] is False and "password" not in str(body)
    assert mail.outbox[-1]["to"] == "ada@acme.test"


async def test_login_blocked_until_email_verified(client):
    await client.post(
        "/auth/register",
        json={"organization_name": "Acme", "full_name": "Ada", "email": "a@acme.test", "password": PASSWORD},
    )
    res = await client.post("/auth/login", json={"email": "a@acme.test", "password": PASSWORD})
    assert res.status_code == 403 and res.json()["error"]["code"] == "email_not_verified"
    ok = await client.post("/auth/verify-email", json={"token": token_from_email("a@acme.test")})
    assert ok.status_code == 200
    assert (await client.post("/auth/login", json={"email": "a@acme.test", "password": PASSWORD})).status_code == 200


async def test_verification_token_is_single_use_and_garbage_rejected(client):
    await client.post(
        "/auth/register",
        json={"organization_name": "Acme", "full_name": "Ada", "email": "a@acme.test", "password": PASSWORD},
    )
    token = token_from_email("a@acme.test")
    assert (await client.post("/auth/verify-email", json={"token": token})).status_code == 200
    assert (await client.post("/auth/verify-email", json={"token": token})).status_code == 400
    assert (await client.post("/auth/verify-email", json={"token": "x" * 40})).status_code == 400


async def test_resend_verification_is_generic(client):
    res = await client.post("/auth/resend-verification", json={"email": "ghost@acme.test"})
    assert res.status_code == 202 and not mail.outbox
    await client.post(
        "/auth/register",
        json={"organization_name": "Acme", "full_name": "Ada", "email": "a@acme.test", "password": PASSWORD},
    )
    before = len(mail.outbox)
    res = await client.post("/auth/resend-verification", json={"email": "a@acme.test"})
    assert res.status_code == 202 and len(mail.outbox) == before + 1


async def test_duplicate_email_rejected(client):
    await register_admin(client)
    res = await client.post(
        "/auth/register",
        json={"organization_name": "Other", "full_name": "X", "email": "admin@acme.test", "password": PASSWORD},
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == "email_taken"


@pytest.mark.parametrize("password", ["short1", "alllettersnodigits", "123456789012345"])
async def test_weak_passwords_rejected(client, password):
    res = await client.post(
        "/auth/register",
        json={"organization_name": "Acme", "full_name": "Ada", "email": "a@acme.test", "password": password},
    )
    assert res.status_code == 422


async def test_login_errors_do_not_reveal_which_part_was_wrong(client):
    await register_admin(client)
    bad_pw = await client.post("/auth/login", json={"email": "admin@acme.test", "password": "wrong-password-1"})
    no_user = await client.post("/auth/login", json={"email": "nobody@acme.test", "password": "wrong-password-1"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()


async def test_account_locks_after_repeated_failures(client):
    await register_admin(client)
    for _ in range(settings.max_failed_logins):
        res = await client.post("/auth/login", json={"email": "admin@acme.test", "password": "wrong-password-1"})
        assert res.status_code == 401
    locked = await client.post("/auth/login", json={"email": "admin@acme.test", "password": PASSWORD})
    assert locked.status_code == 429 and locked.json()["error"]["code"] == "account_locked"


async def test_me_returns_profile_and_permissions(client):
    admin = await register_admin(client)
    res = await client.get("/auth/me", headers=admin.headers)
    body = res.json()
    assert body["email"] == admin.email and body["organization_name"] == "Acme Corp"
    assert "users:manage" in body["permissions"] and body["mfa_enabled"] is False


async def test_garbage_and_wrong_type_tokens_rejected(client):
    admin = await register_admin(client)
    for token in ("garbage", admin.refresh):
        res = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401
    mfa_token = sec.create_mfa_token(uuid.UUID(admin.user_id), uuid.UUID(admin.org_id))
    res = await client.get("/auth/me", headers={"Authorization": f"Bearer {mfa_token}"})
    assert res.status_code == 401


async def test_expired_token_rejected(client):
    admin = await register_admin(client)
    now = datetime.now(UTC)
    expired = jwt.encode(
        {"sub": admin.user_id, "org": admin.org_id, "sid": str(uuid.uuid4()), "type": "access",
         "iss": settings.jwt_issuer, "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)},
        settings.jwt_secret, algorithm="HS256",
    )
    res = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert res.status_code == 401


async def test_token_signed_with_other_secret_rejected(client):
    admin = await register_admin(client)
    now = datetime.now(UTC)
    forged = jwt.encode(
        {"sub": admin.user_id, "org": admin.org_id, "sid": str(uuid.uuid4()), "type": "access",
         "iss": settings.jwt_issuer, "iat": now, "exp": now + timedelta(hours=1)},
        "x" * 40, algorithm="HS256",
    )
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401


async def test_refresh_rotates_tokens(client):
    admin = await register_admin(client)
    res = await client.post("/auth/refresh", json={"refresh_token": admin.refresh})
    assert res.status_code == 200
    new = res.json()
    assert new["refresh_token"] != admin.refresh
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert me.status_code == 200
    again = await client.post("/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert again.status_code == 200


async def test_refresh_reuse_inside_grace_window_is_rejected_but_keeps_session(client):
    admin = await register_admin(client)
    first = (await client.post("/auth/refresh", json={"refresh_token": admin.refresh})).json()
    stale = await client.post("/auth/refresh", json={"refresh_token": admin.refresh})
    assert stale.status_code == 401
    still_ok = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert still_ok.status_code == 200


async def test_refresh_reuse_after_grace_revokes_session(client, monkeypatch):
    monkeypatch.setattr(settings, "refresh_reuse_grace_seconds", 0)
    admin = await register_admin(client)
    first = (await client.post("/auth/refresh", json={"refresh_token": admin.refresh})).json()
    time.sleep(0.05)
    assert (await client.post("/auth/refresh", json={"refresh_token": admin.refresh})).status_code == 401
    # the legitimate holder of the newest token is signed out as well
    assert (await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})).status_code == 401
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"})).status_code == 401


async def test_unknown_refresh_token_rejected(client):
    res = await client.post("/auth/refresh", json={"refresh_token": "x" * 48})
    assert res.status_code == 401


async def test_logout_revokes_access_and_refresh(client):
    admin = await register_admin(client)
    assert (await client.post("/auth/logout", headers=admin.headers)).status_code == 200
    assert (await client.get("/auth/me", headers=admin.headers)).status_code == 401
    assert (await client.post("/auth/refresh", json={"refresh_token": admin.refresh})).status_code == 401


async def test_password_reset_flow(client):
    admin = await register_admin(client)
    res = await client.post("/auth/password/forgot", json={"email": admin.email})
    assert res.status_code == 202
    token = token_from_email(admin.email)
    new_pw = "brand-new-password-42"
    assert (await client.post("/auth/password/reset", json={"token": token, "new_password": new_pw})).status_code == 200
    # previous sessions are gone, old password fails, new one works
    assert (await client.get("/auth/me", headers=admin.headers)).status_code == 401
    assert (await client.post("/auth/login", json={"email": admin.email, "password": PASSWORD})).status_code == 401
    assert (await client.post("/auth/login", json={"email": admin.email, "password": new_pw})).status_code == 200
    # single use
    again = await client.post("/auth/password/reset", json={"token": token, "new_password": "another-password-77"})
    assert again.status_code == 400


async def test_forgot_password_is_generic_for_unknown_email(client):
    res = await client.post("/auth/password/forgot", json={"email": "ghost@acme.test"})
    assert res.status_code == 202 and not mail.outbox


async def test_reset_rejects_weak_password(client):
    admin = await register_admin(client)
    await client.post("/auth/password/forgot", json={"email": admin.email})
    res = await client.post(
        "/auth/password/reset", json={"token": token_from_email(admin.email), "new_password": "short1"}
    )
    assert res.status_code == 422


async def test_password_change_keeps_current_session_and_revokes_others(client):
    admin = await register_admin(client)
    other = await login(client, admin.email)
    res = await client.post(
        "/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "changed-password-55"},
        headers=admin.headers,
    )
    assert res.status_code == 200
    assert (await client.get("/auth/me", headers=admin.headers)).status_code == 200
    assert (await client.get("/auth/me", headers=other.headers)).status_code == 401


async def test_password_change_requires_current_password(client):
    admin = await register_admin(client)
    res = await client.post(
        "/auth/password/change",
        json={"current_password": "wrong-password-1", "new_password": "changed-password-55"},
        headers=admin.headers,
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "invalid_password"


async def test_sessions_list_and_revoke(client):
    admin = await register_admin(client)
    other = await login(client, admin.email)
    listing = (await client.get("/auth/sessions", headers=admin.headers)).json()
    assert len(listing) == 2 and sum(s["current"] for s in listing) == 1
    target = next(s for s in listing if not s["current"])
    assert (await client.delete(f"/auth/sessions/{target['id']}", headers=admin.headers)).status_code == 200
    assert (await client.get("/auth/me", headers=other.headers)).status_code == 401
    assert (await client.delete(f"/auth/sessions/{uuid.uuid4()}", headers=admin.headers)).status_code == 404


async def test_logout_all(client):
    admin = await register_admin(client)
    other = await login(client, admin.email)
    assert (await client.post("/auth/logout-all", headers=admin.headers)).status_code == 200
    for acct in (admin, other):
        assert (await client.get("/auth/me", headers=acct.headers)).status_code == 401


async def test_passwords_are_hashed_with_argon2(client, admin_engine):
    from sqlalchemy import text

    await register_admin(client)
    async with admin_engine.connect() as conn:
        stored = await conn.scalar(text("SELECT password_hash FROM users"))
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


async def test_auth_events_are_audited(client):
    admin = await register_admin(client)
    await client.post("/auth/login", json={"email": admin.email, "password": "wrong-password-1"})
    actions = [r["action"] for r in (await client.get("/audit-logs", headers=admin.headers)).json()]
    assert {"auth.register", "auth.email_verified", "auth.login", "auth.login_failed"} <= set(actions)
