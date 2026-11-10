import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet

APP_PW = os.environ.setdefault("APP_DB_PASSWORD", "test_app_password_0123456789")
DB_HOST = os.environ.get("TEST_DB_HOST", "localhost")
DB_PORT = os.environ.get("TEST_DB_PORT", "5432")
ADMIN_USER = os.environ.get("TEST_DB_ADMIN_USER", "copilot")
ADMIN_PW = os.environ.get("TEST_DB_ADMIN_PASSWORD", "copilot")
TEST_DB = "copilot_test"
ADMIN_URL = f"postgresql+asyncpg://{ADMIN_USER}:{ADMIN_PW}@{DB_HOST}:{DB_PORT}/{TEST_DB}"
MAINT_URL = f"postgresql+asyncpg://{ADMIN_USER}:{ADMIN_PW}@{DB_HOST}:{DB_PORT}/postgres"

os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://copilot_app:{APP_PW}@{DB_HOST}:{DB_PORT}/{TEST_DB}"
os.environ["MIGRATION_DATABASE_URL"] = ADMIN_URL
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-0123456789")
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ["EMAIL_BACKEND"] = "memory"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REQUIRE_EMAIL_VERIFICATION"] = "true"

import asyncio  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app import email as mail  # noqa: E402
from app.main import app  # noqa: E402
from app.ratelimit import reset_rate_limits  # noqa: E402


async def _ensure_database() -> None:
    engine = create_async_engine(MAINT_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with engine.connect() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB})
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    asyncio.run(_ensure_database())
    backend_dir = Path(__file__).resolve().parent.parent
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend_dir, check=True,
        env=os.environ.copy(),
    )


@pytest_asyncio.fixture
async def admin_engine():
    engine = create_async_engine(ADMIN_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_state(admin_engine):
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE audit_logs, mfa_recovery_codes, email_tokens, auth_sessions, "
                "invitations, users, organizations RESTART IDENTITY CASCADE"
            )
        )
    mail.outbox.clear()
    reset_rate_limits()
    yield


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c
