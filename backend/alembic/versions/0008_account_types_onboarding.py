"""Account types (solo_buyer, org_buyer, supplier) and the onboarding profile on organizations.

Every account is an organization: a solo buyer is an organization of one, an org buyer is a team, a supplier
lists inventory. The intake answers describe the business, so they live on the organization row, which is
already confined to its own tenant by row-level security (policy from 0001).

Organizations that exist before this migration become org_buyer accounts with onboarding already completed,
so nobody who is using the product today is sent back through the intake form.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("account_type", sa.String(12), nullable=False, server_default="org_buyer"),
    )
    op.create_check_constraint(
        "ck_organizations_account_type", "organizations", "account_type IN ('solo_buyer','org_buyer','supplier')"
    )
    # Existing rows are backfilled as completed; new rows default to not completed.
    op.add_column(
        "organizations",
        sa.Column("onboarding_completed", sa.Boolean, nullable=False, server_default=sa.text("true")),
    )
    op.alter_column("organizations", "onboarding_completed", server_default=sa.text("false"))
    op.add_column("organizations", sa.Column("onboarding_completed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "organizations",
        sa.Column("onboarding_profile", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_organizations_account_type", "organizations", ["account_type"])


def downgrade() -> None:
    op.drop_index("ix_organizations_account_type", table_name="organizations")
    op.drop_column("organizations", "onboarding_profile")
    op.drop_column("organizations", "onboarding_completed_at")
    op.drop_column("organizations", "onboarding_completed")
    op.drop_constraint("ck_organizations_account_type", "organizations", type_="check")
    op.drop_column("organizations", "account_type")
