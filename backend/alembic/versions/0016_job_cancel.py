"""Jobs can be stopped: a 'cancelled' status, kept apart from 'dead' (failed) so the two read differently."""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_status", "jobs", "status IN ('queued','running','succeeded','dead','cancelled')")


def downgrade() -> None:
    op.execute("UPDATE jobs SET status = 'dead' WHERE status = 'cancelled'")
    op.drop_constraint("ck_jobs_status", "jobs", type_="check")
    op.create_check_constraint("ck_jobs_status", "jobs", "status IN ('queued','running','succeeded','dead')")
