"""Let the application remove invited suppliers from a draft RFQ.

remove_supplier deletes the rfq_suppliers row while the RFQ is still a draft, but 0004 only granted
SELECT, INSERT, UPDATE on that table, so the delete failed with "permission denied".
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"


def upgrade() -> None:
    op.execute(f"GRANT DELETE ON rfq_suppliers TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE DELETE ON rfq_suppliers FROM {APP_ROLE}")
