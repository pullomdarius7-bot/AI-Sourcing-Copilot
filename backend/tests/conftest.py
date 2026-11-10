import os
import subprocess
import sys
import tempfile
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
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_LOCAL_DIR"] = tempfile.mkdtemp(prefix="asc-uploads-")
os.environ["MINIO_ENDPOINT"] = ""
os.environ["SCAN_BACKEND"] = "builtin"
os.environ["AI_PROVIDER_ORDER"] = ""
os.environ["JOB_BACKOFF_BASE_SECONDS"] = "0"
os.environ["MAX_UPLOAD_MB"] = "1"
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
from app.agent import audit as agent_audit  # noqa: E402
from app.ai.service import AIService, set_ai_service  # noqa: E402
from app.main import app  # noqa: E402
from app.ratelimit import reset_rate_limits  # noqa: E402
from app.storage import set_storage  # noqa: E402


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
                "TRUNCATE knowledge_chunks, agent_reports, agent_messages, agent_runs, agent_conversations, award_recommendations, fx_rates, unit_conversions, counters, domain_events, comparisons, rfq_quote_lines, rfq_quotes, rfq_packages, rfq_suppliers, rfq_items, rfqs, "
                "supplier_qualification_checks, qualification_criteria, supplier_contacts, supplier_category_links, supplier_categories, suppliers, "
                "review_events, parameters, requirements, extraction_runs, jobs, ai_audit_logs, document_extractions, documents, audit_logs, "
                "mfa_recovery_codes, email_tokens, auth_sessions, invitations, users, "
                "organizations RESTART IDENTITY CASCADE"
            )
        )
    mail.outbox.clear()
    reset_rate_limits()
    agent_audit.reset()
    set_ai_service(AIService([]))  # no providers unless a test installs one
    set_storage(None)
    yield
    set_ai_service(None)
    set_storage(None)


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c


@pytest_asyncio.fixture
async def worker():
    from app.jobs.worker import Worker

    return Worker("test-worker")
