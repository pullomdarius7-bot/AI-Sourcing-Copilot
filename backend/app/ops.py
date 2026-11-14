"""Command-line tools for the platform owner. Run from the backend folder with the same environment as the app:

    python -m app.ops grant-platform-admin you@example.com    # give an account the back office
    python -m app.ops revoke-platform-admin you@example.com
    python -m app.ops list-platform-admins
    python -m app.ops comp-account you@example.com            # the account of this user uses AI for free
    python -m app.ops uncomp-account someone@example.com
    python -m app.ops health                                  # the same checks as the back office

Platform admin can only be granted here, by someone with access to the server, never through the website.
"""
import argparse
import asyncio
import sys

from sqlalchemy import func, select

from app.db import SessionLocal, set_system
from app.models import PlatformAudit, User

MARK = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


async def _set_admin(email: str, value: bool) -> int:
    async with SessionLocal() as db:
        await set_system(db)
        user = await db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()).with_for_update())
        if user is None:
            print(f"No account uses {email}. Register on the website first, then run this again.")
            return 1
        user.is_platform_admin = value
        db.add(PlatformAudit(actor_email="command line", action="admin.granted" if value else "admin.revoked",
                             target_type="user", target_id=str(user.id), meta={"email": user.email}))
        await db.commit()
        print(f"{user.email} {'can now open the back office (/platform).' if value else 'no longer has the back office.'}")
        if value and not user.mfa_enabled:
            print("Note: turn on two-factor sign-in for this account (Settings > Security), unless "
                  "PLATFORM_ADMIN_REQUIRES_MFA=false.")
        return 0


async def _set_comp(email: str, value: bool) -> int:
    from app.models import Organization

    async with SessionLocal() as db:
        await set_system(db)
        user = await db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
        if user is None:
            print(f"No account uses {email}. Register on the website first, then run this again.")
            return 1
        org = await db.get(Organization, user.organization_id, with_for_update=True)
        org.billing_exempt = value
        db.add(PlatformAudit(actor_email="command line", action="account.comped" if value else "account.uncomped",
                             target_type="organization", target_id=str(org.id), meta={"email": user.email}))
        await db.commit()
        print(f"{org.name} ({user.email}) {'is now complimentary: AI is free and unlimited.' if value else 'pays normally again.'}")
        return 0


async def _list_admins() -> int:
    async with SessionLocal() as db:
        await set_system(db)
        admins = (await db.scalars(select(User).where(User.is_platform_admin.is_(True)).order_by(User.email))).all()
        for u in admins:
            print(f"{u.email}\t{'active' if u.is_active else 'inactive'}\t{'2FA on' if u.mfa_enabled else '2FA off'}")
        if not admins:
            print("No platform admins yet. Run: python -m app.ops grant-platform-admin you@example.com")
        return 0


async def _health() -> int:
    from app.platform.health import run_checks

    async with SessionLocal() as db:
        try:
            checks = await run_checks(db)
        except Exception as exc:
            print(f"FAIL  Database: cannot connect ({type(exc).__name__}: {exc})")
            return 2
    width = max(len(c["name"]) for c in checks)
    for c in checks:
        print(f"{MARK[c['status']]}  {c['name'].ljust(width)}  {c['detail']}")
    return 2 if any(c["status"] == "fail" for c in checks) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ops", description="Platform owner tools")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("grant-platform-admin", "revoke-platform-admin"):
        sub.add_parser(name).add_argument("email")
    sub.add_parser("list-platform-admins")
    for name in ("comp-account", "uncomp-account"):
        sub.add_parser(name).add_argument("email")
    sub.add_parser("health")
    args = parser.parse_args(argv)
    if args.command == "grant-platform-admin":
        return asyncio.run(_set_admin(args.email, True))
    if args.command == "revoke-platform-admin":
        return asyncio.run(_set_admin(args.email, False))
    if args.command in ("comp-account", "uncomp-account"):
        return asyncio.run(_set_comp(args.email, args.command == "comp-account"))
    if args.command == "list-platform-admins":
        return asyncio.run(_list_admins())
    return asyncio.run(_health())


if __name__ == "__main__":
    sys.exit(main())
