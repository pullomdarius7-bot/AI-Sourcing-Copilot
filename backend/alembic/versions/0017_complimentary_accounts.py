"""Complimentary accounts: the platform owner's own accounts (and any it chooses) use AI without credits or a plan.

Set only from the command line (python -m app.ops comp-account EMAIL) or the back office, never by customers.
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("billing_exempt", sa.Boolean, nullable=False, server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("organizations", "billing_exempt")
