"""Platform administration: the owner's back office.

users.is_platform_admin marks the people who run the platform. It can only be set from the command line
(python -m app.ops grant-platform-admin <email>), never through the API. platform_audit is the append-only record of
everything done in the back office; only the system can read or write it.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"


def upgrade() -> None:
    op.add_column("users", sa.Column("is_platform_admin", sa.Boolean, nullable=False, server_default=sa.text("false")))
    op.create_table(
        "platform_audit",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("actor_user_id", pg.UUID(as_uuid=True)),
        sa.Column("actor_email", sa.String(320)),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target_type", sa.String(30)),
        sa.Column("target_id", sa.String(320)),
        sa.Column("meta", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("ip", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_platform_audit_created", "platform_audit", [sa.text("id DESC")])
    op.execute("ALTER TABLE platform_audit ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform_audit FORCE ROW LEVEL SECURITY")
    predicate = "(current_setting('app.system', true) = 'on')"
    op.execute(f"CREATE POLICY system_only ON platform_audit USING {predicate} WITH CHECK {predicate}")
    op.execute(f"GRANT SELECT, INSERT ON platform_audit TO {APP_ROLE}")
    op.execute(f"GRANT DELETE ON email_suppressions TO {APP_ROLE}")  # lifting an opt-out on the business's request
    op.execute(f"GRANT SELECT ON alembic_version TO {APP_ROLE}")  # the health check reports the schema version


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ON alembic_version FROM {APP_ROLE}")
    op.execute(f"REVOKE DELETE ON email_suppressions FROM {APP_ROLE}")
    op.drop_table("platform_audit")
    op.drop_column("users", "is_platform_admin")
