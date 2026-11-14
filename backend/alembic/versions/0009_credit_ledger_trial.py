"""Credit ledger and trial window.

credit_ledgers is the append-only record of every credit granted or spent by an account (one credit = one
manifest analysis). organizations.credit_balance is the running balance, changed only together with a ledger
row while the organization row is locked, and can never go below zero. trial_ends_at closes the free trial
by time; the balance closes it by usage, whichever comes first.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
PREDICATE = (
    "(organization_id::text = current_setting('app.current_org', true) "
    "OR current_setting('app.system', true) = 'on')"
)


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("credit_balance", sa.Integer, nullable=False, server_default=sa.text("0"))
    )
    op.create_check_constraint("ck_organizations_credit_balance", "organizations", "credit_balance >= 0")
    op.add_column("organizations", sa.Column("trial_ends_at", sa.DateTime(timezone=True)))

    op.create_table(
        "credit_ledgers",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "organization_id", pg.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("delta", sa.Integer, nullable=False),
        sa.Column("balance_after", sa.Integer, nullable=False),
        # Idempotency key: the same grant or charge can never be posted twice for one organization.
        sa.Column("reference", sa.String(120)),
        sa.Column("note", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("delta <> 0", name="ck_credit_ledgers_delta"),
        sa.CheckConstraint("balance_after >= 0", name="ck_credit_ledgers_balance"),
        sa.CheckConstraint(
            "kind IN ('trial_grant','plan_grant','purchase','usage','refund','adjustment','expiry')",
            name="ck_credit_ledgers_kind",
        ),
    )
    op.create_index("ix_credit_ledgers_org_id_id", "credit_ledgers", ["organization_id", sa.text("id DESC")])
    op.create_index(
        "uq_credit_ledgers_org_reference", "credit_ledgers", ["organization_id", "reference"], unique=True,
        postgresql_where=sa.text("reference IS NOT NULL"),
    )
    op.execute("ALTER TABLE credit_ledgers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE credit_ledgers FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON credit_ledgers USING {PREDICATE} WITH CHECK {PREDICATE}")
    # Append-only for the application: entries are never edited or removed.
    op.execute(f"GRANT SELECT, INSERT ON credit_ledgers TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("credit_ledgers")
    op.drop_column("organizations", "trial_ends_at")
    op.drop_constraint("ck_organizations_credit_balance", "organizations", type_="check")
    op.drop_column("organizations", "credit_balance")
