import hashlib
import hmac
import re
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

settings = get_settings()
_hasher = PasswordHasher()
DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))
_fernet = Fernet(settings.mfa_encryption_key.encode())


class TokenError(Exception):
    pass


# --- Passwords ---------------------------------------------------------------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problem(password: str) -> str | None:
    if len(password) < 12:
        return "Password must be at least 12 characters"
    if len(password) > 128:
        return "Password must be at most 128 characters"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
        return "Password must contain at least one letter and one digit"
    return None


# --- Opaque tokens -----------------------------------------------------------
def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


# --- JWT ---------------------------------------------------------------------
def _encode(claims: dict[str, Any], minutes: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        "jti": uuid.uuid4().hex,
        **claims,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def create_access_token(user_id: uuid.UUID, org_id: uuid.UUID, role: str, sid: uuid.UUID) -> str:
    return _encode(
        {"sub": str(user_id), "org": str(org_id), "role": role, "sid": str(sid), "type": "access"},
        settings.access_token_minutes,
    )


def create_mfa_token(user_id: uuid.UUID, org_id: uuid.UUID) -> str:
    return _encode(
        {"sub": str(user_id), "org": str(org_id), "type": "mfa"}, settings.mfa_token_minutes
    )


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise TokenError("wrong token type")
    return payload


# --- Secret encryption (TOTP seeds at rest) -----------------------------------
def encrypt_secret(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet.decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise TokenError("cannot decrypt secret") from exc


# --- TOTP --------------------------------------------------------------------
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=settings.mfa_issuer)


def verify_totp(secret: str, code: str, last_step: int) -> int | None:
    """Return the matched 30-second step, or None. Steps at or before last_step are replays."""
    if not re.fullmatch(r"[0-9]{6}", code):
        return None
    totp = pyotp.TOTP(secret)
    current = int(time.time()) // 30
    for step in (current - 1, current, current + 1):
        if step > last_step and hmac.compare_digest(totp.at(step * 30), code):
            return step
    return None


def new_recovery_codes(count: int = 10) -> list[str]:
    return [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(count)]
