"""Initial schema: organizations, users, sessions, tokens, MFA, invitations, audit, RLS.

Revision ID: 0001
Revises:
"""
import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
TENANT_TABLES = [
    "users",
    "auth_sessions",
    "email_tokens",
    "mfa_recovery_codes",
    "invitations",
    "audit_logs",
]
TENANT_PREDICATE = (
    "(organization_id::text = current_setting('app.current_org', true) "
    "OR current_setting('app.system', true) = 'on')"
)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _org_fk() -> sa.Column:
    return sa.Column(
        "organization_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def _user_fk() -> sa.Column:
    return sa.Column(
        "user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def _ts(name: str, nullable: bool = True, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default_now else None,
    )


def upgrade() -> None:
    password = os.environ.get("APP_DB_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{16,128}", password):
        raise RuntimeError("APP_DB_PASSWORD must be 16-128 chars of [A-Za-z0-9_-]")

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS;
            ELSE
                ALTER ROLE {APP_ROLE} WITH LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$;
        """
    )

    op.create_table(
        "organizations",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
    )

    op.create_table(
        "users",
        _uuid_pk(),
        _org_fk(),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _ts("email_verified_at"),
        sa.Column("mfa_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("mfa_secret_enc", sa.Text),
        sa.Column("mfa_pending_secret_enc", sa.Text),
        sa.Column("mfa_last_step", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("failed_login_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        _ts("locked_until"),
        _ts("last_login_at"),
        _ts("password_changed_at", nullable=False, default_now=True),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
        sa.CheckConstraint(
            "role IN ('requester','buyer','approver','admin','auditor')", name="ck_users_role"
        ),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    op.create_table(
        "auth_sessions",
        _uuid_pk(),
        _org_fk(),
        _user_fk(),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prev_token_hash", sa.String(64)),
        _ts("rotated_at", nullable=False, default_now=True),
        sa.Column("user_agent", sa.String(300)),
        sa.Column("ip", sa.String(64)),
        _ts("created_at", nullable=False, default_now=True),
        _ts("last_used_at", nullable=False, default_now=True),
        _ts("expires_at", nullable=False),
        _ts("revoked_at"),
        sa.Column("revoked_reason", sa.String(40)),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_prev_token_hash", "auth_sessions", ["prev_token_hash"])
    op.create_index("ix_auth_sessions_organization_id", "auth_sessions", ["organization_id"])

    op.create_table(
        "email_tokens",
        _uuid_pk(),
        _org_fk(),
        _user_fk(),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        _ts("expires_at", nullable=False),
        _ts("used_at"),
        _ts("created_at", nullable=False, default_now=True),
        sa.CheckConstraint("purpose IN ('verify_email','reset_password')", name="ck_email_purpose"),
    )
    op.create_index("ix_email_tokens_user_id", "email_tokens", ["user_id"])
    op.create_index("ix_email_tokens_organization_id", "email_tokens", ["organization_id"])

    op.create_table(
        "mfa_recovery_codes",
        _uuid_pk(),
        _org_fk(),
        _user_fk(),
        sa.Column("code_hash", sa.String(64), nullable=False),
        _ts("used_at"),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_mfa_recovery_codes_user_id", "mfa_recovery_codes", ["user_id"])
    op.create_index(
        "ix_mfa_recovery_codes_organization_id", "mfa_recovery_codes", ["organization_id"]
    )

    op.create_table(
        "invitations",
        _uuid_pk(),
        _org_fk(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        _ts("expires_at", nullable=False),
        _ts("accepted_at"),
        _ts("revoked_at"),
        _ts("created_at", nullable=False, default_now=True),
        sa.CheckConstraint("email = lower(email)", name="ck_invitations_email_lower"),
        sa.CheckConstraint(
            "role IN ('requester','buyer','approver','admin','auditor')", name="ck_invitations_role"
        ),
    )
    op.create_index("ix_invitations_organization_id", "invitations", ["organization_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        _org_fk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("meta", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False, default_now=True),
    )
    op.create_index("ix_audit_logs_org_id_id", "audit_logs", ["organization_id", sa.text("id DESC")])

    # --- Row-level security -------------------------------------------------
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    org_predicate = (
        "(id::text = current_setting('app.current_org', true) "
        "OR current_setting('app.system', true) = 'on')"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation ON organizations "
        f"USING {org_predicate} WITH CHECK {org_predicate}"
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING {TENANT_PREDICATE} WITH CHECK {TENANT_PREDICATE}"
        )

    # --- Least-privilege grants for the application role ---------------------
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON organizations TO {APP_ROLE}")
    for table in ("users", "auth_sessions", "email_tokens", "invitations"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON mfa_recovery_codes TO {APP_ROLE}")
    # The audit log is append-only for the application role.
    op.execute(f"GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE}")


def downgrade() -> None:
    for table in (
        "audit_logs",
        "invitations",
        "mfa_recovery_codes",
        "email_tokens",
        "auth_sessions",
        "users",
        "organizations",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
