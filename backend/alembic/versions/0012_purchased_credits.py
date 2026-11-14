"""Pay-as-you-go credit top-ups.

organizations.purchased_credits is the part of credit_balance that was bought outright. Plan credits are spent
first; purchased credits roll over at renewal and are not removed when a subscription ends.
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("purchased_credits", sa.Integer, nullable=False, server_default=sa.text("0"))
    )
    op.create_check_constraint(
        "ck_organizations_purchased_credits", "organizations", "purchased_credits >= 0 AND purchased_credits <= credit_balance"
    )


def downgrade() -> None:
    op.drop_constraint("ck_organizations_purchased_credits", "organizations", type_="check")
    op.drop_column("organizations", "purchased_credits")
