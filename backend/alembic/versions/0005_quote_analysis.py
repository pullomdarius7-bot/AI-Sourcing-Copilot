"""Sprint 5: quote analysis reference data (exchange rates, unit rules), MOQ, requested terms, award recommendations.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
PREDICATE = (
    "(organization_id::text = current_setting('app.current_org', true) "
    "OR current_setting('app.system', true) = 'on')"
)
TABLES = ["fx_rates", "unit_conversions", "award_recommendations"]


def _pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _org() -> sa.Column:
    return sa.Column(
        "organization_id", pg.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def _user(name: str) -> sa.Column:
    return sa.Column(name, pg.UUID(as_uuid=True), sa.ForeignKey("users.id"))


def _ts(name: str, nullable: bool = True, now: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=sa.text("now()") if now else None)


def upgrade() -> None:
    op.create_table(
        "fx_rates", _pk(), _org(),
        sa.Column("base", sa.String(3), nullable=False),
        sa.Column("quote", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(24, 10), nullable=False),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("source", sa.String(200)),
        _user("created_by"), _ts("created_at", False, True),
        sa.CheckConstraint("base ~ '^[A-Z]{3}$' AND quote ~ '^[A-Z]{3}$' AND base <> quote", name="ck_fx_rates_pair"),
        sa.CheckConstraint("rate > 0", name="ck_fx_rates_positive"),
        sa.UniqueConstraint("organization_id", "base", "quote", "valid_from", name="uq_fx_rates_pair_date"),
    )
    op.create_index("ix_fx_rates_lookup", "fx_rates", ["organization_id", "base", "quote", "valid_from"])

    op.create_table(
        "unit_conversions", _pk(), _org(),
        sa.Column("from_unit", sa.String(30), nullable=False),
        sa.Column("to_unit", sa.String(30), nullable=False),
        sa.Column("factor", sa.Numeric(30, 12), nullable=False),
        sa.Column("note", sa.String(300)),
        _user("created_by"), _ts("created_at", False, True),
        sa.CheckConstraint("factor > 0", name="ck_unit_conversions_positive"),
        sa.CheckConstraint("from_unit <> to_unit", name="ck_unit_conversions_distinct"),
        sa.UniqueConstraint("organization_id", "from_unit", "to_unit", name="uq_unit_conversions_pair"),
    )

    op.create_table(
        "award_recommendations", _pk(), _org(),
        sa.Column("rfq_id", pg.UUID(as_uuid=True), sa.ForeignKey("rfqs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("comparison_id", pg.UUID(as_uuid=True), sa.ForeignKey("comparisons.id"), nullable=False),
        sa.Column("supplier_id", pg.UUID(as_uuid=True), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("snapshot", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _user("submitted_by"), _ts("submitted_at", False, True),
        _user("decided_by"), _ts("decided_at"), sa.Column("decision_note", sa.Text),
        sa.CheckConstraint("status IN ('pending','approved','rejected','withdrawn')", name="ck_recommendations_status"),
        sa.CheckConstraint("length(trim(rationale)) > 0", name="ck_recommendations_rationale"),
        # a decision is always recorded with who made it; a rejection always says why
        sa.CheckConstraint(
            "status = 'pending' OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)", name="ck_recommendations_decided"
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR length(trim(coalesce(decision_note, ''))) > 0", name="ck_recommendations_reject_note"
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR decided_by IS DISTINCT FROM submitted_by", name="ck_recommendations_four_eyes"
        ),
    )
    op.create_index("ix_recommendations_rfq", "award_recommendations", ["rfq_id", "submitted_at"])
    op.create_index(
        "uq_recommendations_one_pending", "award_recommendations", ["rfq_id"], unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.add_column("rfqs", sa.Column("award_recommendation_id", pg.UUID(as_uuid=True)))
    op.add_column("rfqs", sa.Column("required_by", sa.Date))
    op.add_column("rfqs", sa.Column("requested_incoterms", sa.String(10)))
    op.add_column("rfqs", sa.Column("requested_payment_terms", sa.String(200)))
    op.add_column("rfq_quote_lines", sa.Column("moq", sa.Float))
    op.create_check_constraint("ck_quote_lines_moq", "rfq_quote_lines", "moq IS NULL OR moq > 0")
    op.drop_constraint("ck_quotes_source", "rfq_quotes", type_="check")
    op.create_check_constraint("ck_quotes_source", "rfq_quotes", "source IN ('analysis','manual','import')")

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING {PREDICATE} WITH CHECK {PREDICATE}")
    for table in ("fx_rates", "unit_conversions"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    # recommendations are decided, never deleted
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON award_recommendations TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_constraint("ck_quotes_source", "rfq_quotes", type_="check")
    op.execute("DELETE FROM rfq_quotes WHERE source = 'import'")
    op.create_check_constraint("ck_quotes_source", "rfq_quotes", "source IN ('analysis','manual')")
    op.drop_constraint("ck_quote_lines_moq", "rfq_quote_lines", type_="check")
    op.drop_column("rfq_quote_lines", "moq")
    for column in ("requested_payment_terms", "requested_incoterms", "required_by", "award_recommendation_id"):
        op.drop_column("rfqs", column)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
