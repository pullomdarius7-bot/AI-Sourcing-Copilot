"""Health checks for the back office and the command line (python -m app.ops health).

Each check returns ok, warn or fail with a plain-language detail, so problems can be found and fixed by hand.
"""
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import get_ai_service
from app.config import get_settings
from app.db import set_system
from app.models import Job, SupplierInvite
from app.sourcing.search import get_search
from app.storage import StorageError, get_storage

settings = get_settings()


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str


async def run_checks(db: AsyncSession) -> list[dict[str, Any]]:
    await set_system(db)
    checks: list[Check] = []

    try:
        await db.execute(text("SELECT 1"))
        checks.append(Check("Database", "ok", "Connected"))
    except Exception as exc:  # pragma: no cover - the request would not get here without a database
        return [asdict(Check("Database", "fail", f"Not reachable: {type(exc).__name__}"))]

    try:
        version = await db.scalar(text("SELECT version_num FROM alembic_version"))
        checks.append(Check("Database schema", "ok", f"Migration {version}"))
    except Exception:
        await db.rollback()
        await set_system(db)
        checks.append(Check("Database schema", "warn", "Could not read the migration version"))

    key = f"_health/{uuid.uuid4().hex}"
    try:
        storage = get_storage()
        backend = await storage.put(key, b"ok", "text/plain")
        ok = await storage.get(backend, key) == b"ok"
        await storage.delete(backend, key)
        checks.append(Check("File storage", "ok" if ok else "fail", f"Write and read work ({backend})"))
    except (StorageError, OSError) as exc:
        checks.append(Check("File storage", "fail", f"Cannot write files: {exc}"))

    now = datetime.now(UTC)
    finished = await db.scalar(select(func.max(Job.finished_at)))
    waiting = await db.scalar(select(func.count()).select_from(Job).where(
        Job.status == "queued", Job.run_at < now - timedelta(minutes=5))) or 0
    beat = settings.worker_heartbeat_file
    beat_age = time.time() - os.path.getmtime(beat) if os.path.exists(beat) else None
    if waiting:
        checks.append(Check("Background worker", "fail",
                            f"{waiting} job(s) waiting more than 5 minutes: is the worker running?"))
    elif beat_age is not None and beat_age < 120:
        checks.append(Check("Background worker", "ok", f"Heartbeat {int(beat_age)} s ago"))
    else:
        last = f"last job finished {finished:%Y-%m-%d %H:%M} UTC" if finished else "no job has run yet"
        checks.append(Check("Background worker", "warn", f"No recent heartbeat on this machine ({last})"))

    dead = await db.scalar(select(func.count()).select_from(Job).where(Job.status == "dead")) or 0
    checks.append(Check("Failed jobs", "warn" if dead else "ok",
                        f"{dead} job(s) failed for good: retry them under Jobs" if dead else "None"))

    providers = [p.name for p in get_ai_service().providers]
    checks.append(Check("AI providers", "ok" if providers else "warn",
                        ", ".join(providers) if providers else "None configured: analyses use rules only"))
    checks.append(Check("Web search", "ok" if get_search() else "warn",
                        settings.search_provider if get_search() else "Not configured: AI sourcing uses the marketplace only"))
    checks.append(Check("Payments (Stripe)",
                        "ok" if settings.stripe_secret_key and settings.stripe_webhook_secret else "warn",
                        "Configured" if settings.stripe_secret_key and settings.stripe_webhook_secret
                        else "STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET not set: nobody can subscribe"))
    captured = settings.email_backend == "smtp" and settings.smtp_host == "mailpit"
    if settings.email_backend != "smtp":
        checks.append(Check("Email", "warn", f"'{settings.email_backend}' backend: emails are written to the log, not sent"))
    elif captured:
        checks.append(Check("Email", "warn", "Captured by Mailpit: emails reach nobody. Set SMTP_* to a real mail service"))
    else:
        checks.append(Check("Email", "ok", f"SMTP {settings.smtp_host}:{settings.smtp_port}"))
    held = await db.scalar(select(func.count()).select_from(SupplierInvite).where(SupplierInvite.status == "held")) or 0
    if settings.supplier_invites != "auto":
        checks.append(Check("Supplier invitations", "warn", f"Mode '{settings.supplier_invites}': nothing is sent"))
    elif not settings.invite_sender_postal_address.strip():
        checks.append(Check("Supplier invitations", "warn",
                            f"INVITE_SENDER_POSTAL_ADDRESS not set: {held} invitation(s) held"))
    elif captured:
        checks.append(Check("Supplier invitations", "warn",
                            "Recorded as sent but captured by Mailpit: set SUPPLIER_INVITES=review until real email works"))
    else:
        checks.append(Check("Supplier invitations", "ok", f"Sending ({held} held)"))
    checks.append(Check("Virus scanning", "ok" if settings.scan_backend in ("both", "clamav") else "warn",
                        settings.scan_backend if settings.scan_backend != "builtin"
                        else "Built-in checks only (ClamAV not used)"))
    await db.rollback()
    return [asdict(c) for c in checks]
