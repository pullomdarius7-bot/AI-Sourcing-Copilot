"""Sprint 4: suppliers, qualification, RFQs, packages, quotes, comparisons, domain events.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
PREDICATE = (
    "(organization_id::text = current_setting('app.current_org', true) "
    "OR current_setting('app.system', true) = 'on')"
)
TABLES = [
    "counters", "supplier_categories", "suppliers", "supplier_category_links", "supplier_contacts",
    "qualification_criteria", "supplier_qualification_checks", "rfqs", "rfq_items", "rfq_suppliers",
    "rfq_packages", "rfq_quotes", "rfq_quote_lines", "comparisons", "domain_events",
]


def _pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _org() -> sa.Column:
    return sa.Column(
        "organization_id", pg.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def _fk(name: str, table: str, nullable: bool = False, cascade: bool = False) -> sa.Column:
    return sa.Column(
        name, pg.UUID(as_uuid=True), sa.ForeignKey(f"{table}.id", ondelete="CASCADE" if cascade else None),
        nullable=nullable,
    )


def _ts(name: str, nullable: bool = True, now: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=sa.text("now()") if now else None)


def _user(name: str) -> sa.Column:
    return sa.Column(name, pg.UUID(as_uuid=True), sa.ForeignKey("users.id"))


def upgrade() -> None:
    op.create_table(
        "counters", _org(),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("value", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("organization_id", "name"),
    )

    op.create_table(
        "supplier_categories", _pk(), _org(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        _fk("parent_id", "supplier_categories", nullable=True),
        sa.Column("description", sa.Text),
        _ts("created_at", False, True),
    )
    op.create_index("uq_supplier_categories_slug", "supplier_categories", ["organization_id", "slug"], unique=True)

    op.create_table(
        "suppliers", _pk(), _org(),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("legal_name", sa.String(300)),
        sa.Column("status", sa.String(16), nullable=False, server_default="prospect"),
        sa.Column("country", sa.String(2)),
        sa.Column("website", sa.String(300)),
        sa.Column("tax_id", sa.String(60)),
        sa.Column("address", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("payment_terms", sa.String(120)),
        sa.Column("currency", sa.String(3)),
        sa.Column("typical_lead_time_days", sa.Integer),
        sa.Column("tags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        _user("created_by"), _user("submitted_by"), _ts("submitted_at"), _user("decided_by"), _ts("decided_at"),
        sa.Column("status_note", sa.Text),
        _ts("created_at", False, True), _ts("updated_at", False, True),
        sa.CheckConstraint(
            "status IN ('prospect','under_review','qualified','disqualified','blocked')", name="ck_suppliers_status"
        ),
        sa.CheckConstraint("country IS NULL OR country ~ '^[A-Z]{2}$'", name="ck_suppliers_country"),
        sa.CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_suppliers_currency"),
    )
    op.create_index("uq_suppliers_code", "suppliers", ["organization_id", "code"], unique=True)
    op.create_index("uq_suppliers_name", "suppliers", ["organization_id", sa.text("lower(name)")], unique=True)
    op.create_index("ix_suppliers_org_status", "suppliers", ["organization_id", "status"])

    op.create_table(
        "supplier_category_links", _org(),
        _fk("supplier_id", "suppliers", cascade=True), _fk("category_id", "supplier_categories", cascade=True),
        sa.PrimaryKeyConstraint("supplier_id", "category_id"),
    )
    op.create_index("ix_supplier_category_links_org", "supplier_category_links", ["organization_id", "category_id"])

    op.create_table(
        "supplier_contacts", _pk(), _org(), _fk("supplier_id", "suppliers", cascade=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("phone", sa.String(60)),
        sa.Column("role", sa.String(120)),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _ts("created_at", False, True),
        sa.CheckConstraint("email = lower(email)", name="ck_supplier_contacts_email_lower"),
        sa.UniqueConstraint("supplier_id", "email", name="uq_supplier_contacts_email"),
    )
    op.create_index("ix_supplier_contacts_supplier", "supplier_contacts", ["supplier_id"])
    op.create_index(
        "uq_supplier_contacts_primary", "supplier_contacts", ["supplier_id"], unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "qualification_criteria", _pk(), _org(),
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("required", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("weight", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _ts("created_at", False, True),
        sa.CheckConstraint("weight BETWEEN 1 AND 10", name="ck_criteria_weight"),
    )
    op.create_index("uq_qualification_criteria_key", "qualification_criteria", ["organization_id", "key"], unique=True)

    op.create_table(
        "supplier_qualification_checks", _pk(), _org(),
        _fk("supplier_id", "suppliers", cascade=True), _fk("criterion_id", "qualification_criteria"),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text),
        sa.Column("evidence_document_id", pg.UUID(as_uuid=True)),
        sa.Column("expires_at", sa.Date),
        _user("checked_by"), _ts("checked_at"),
        sa.CheckConstraint("status IN ('pending','passed','failed','waived')", name="ck_checks_status"),
        sa.CheckConstraint("expires_at IS NULL OR status = 'passed'", name="ck_checks_expiry_only_passed"),
        sa.UniqueConstraint("supplier_id", "criterion_id", name="uq_checks_supplier_criterion"),
    )
    op.create_index("ix_checks_org", "supplier_qualification_checks", ["organization_id"])

    op.create_table(
        "rfqs", _pk(), _org(),
        sa.Column("number", sa.String(40), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        _fk("source_run_id", "extraction_runs", nullable=True), _fk("category_id", "supplier_categories", nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("response_due_date", sa.Date),
        sa.Column("message", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("content_sha256", sa.String(64)),
        _user("created_by"), _user("submitted_by"), _ts("submitted_at"), _user("approved_by"), _ts("approved_at"),
        _ts("sent_at"), _ts("closed_at"),
        sa.Column("decision_note", sa.Text),
        _fk("awarded_supplier_id", "suppliers", nullable=True),
        sa.Column("award_comparison_id", pg.UUID(as_uuid=True)),
        _user("awarded_by"), _ts("awarded_at"),
        sa.Column("award_note", sa.Text),
        _ts("created_at", False, True), _ts("updated_at", False, True),
        sa.CheckConstraint(
            "status IN ('draft','pending_approval','approved','sent','closed','awarded','cancelled')",
            name="ck_rfqs_status",
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_rfqs_currency"),
        # an award is always a recorded human decision tied to a comparison
        sa.CheckConstraint(
            "status <> 'awarded' OR (awarded_supplier_id IS NOT NULL AND awarded_by IS NOT NULL "
            "AND award_comparison_id IS NOT NULL AND award_note IS NOT NULL)",
            name="ck_rfqs_award_recorded",
        ),
        # nothing leaves the building without an approval
        sa.CheckConstraint(
            "status NOT IN ('approved','sent','closed','awarded') OR (approved_by IS NOT NULL AND content_sha256 IS NOT NULL)",
            name="ck_rfqs_approved_before_send",
        ),
    )
    op.create_index("uq_rfqs_number", "rfqs", ["organization_id", "number"], unique=True)
    op.create_index("ix_rfqs_org_status", "rfqs", ["organization_id", "status"])

    op.create_table(
        "rfq_items", _pk(), _org(), _fk("rfq_id", "rfqs", cascade=True),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("kind", sa.String(12), nullable=False, server_default="requirement"),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="other"),
        sa.Column("priority", sa.String(12), nullable=False, server_default="unknown"),
        sa.Column("quantity", sa.Float),
        sa.Column("unit", sa.String(30)),
        sa.Column("parameters", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_requirement_id", pg.UUID(as_uuid=True)),
        _ts("created_at", False, True),
        sa.CheckConstraint("kind IN ('requirement','manual')", name="ck_rfq_items_kind"),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_rfq_items_quantity"),
    )
    op.create_index("ix_rfq_items_rfq", "rfq_items", ["rfq_id", "position"])

    op.create_table(
        "rfq_suppliers", _pk(), _org(), _fk("rfq_id", "rfqs", cascade=True), _fk("supplier_id", "suppliers"),
        sa.Column("status", sa.String(10), nullable=False, server_default="invited"),
        _ts("sent_at"), _ts("created_at", False, True),
        sa.CheckConstraint("status IN ('invited','sending','sent','failed','quoted','declined')", name="ck_rfq_suppliers_status"),
        sa.UniqueConstraint("rfq_id", "supplier_id", name="uq_rfq_suppliers"),
    )
    op.create_index("ix_rfq_suppliers_org", "rfq_suppliers", ["organization_id"])

    op.create_table(
        "rfq_packages", _pk(), _org(), _fk("rfq_id", "rfqs", cascade=True), _fk("supplier_id", "suppliers"),
        sa.Column("rfq_version", sa.Integer, nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("to_addresses", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("storage_backend", sa.String(20), nullable=False),
        sa.Column("storage_key", sa.String(400), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("manifest", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("send_status", sa.String(10), nullable=False, server_default="not_sent"),
        _ts("sent_at"), sa.Column("send_error", sa.Text), _ts("created_at", False, True),
        sa.CheckConstraint("send_status IN ('not_sent','queued','sent','failed')", name="ck_rfq_packages_send_status"),
        sa.UniqueConstraint("rfq_id", "supplier_id", "rfq_version", name="uq_rfq_packages_version"),
    )
    op.create_index("ix_rfq_packages_org", "rfq_packages", ["organization_id"])

    op.create_table(
        "rfq_quotes", _pk(), _org(), _fk("rfq_id", "rfqs", cascade=True), _fk("supplier_id", "suppliers"),
        sa.Column("document_id", pg.UUID(as_uuid=True)), sa.Column("extraction_id", pg.UUID(as_uuid=True)),
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(3)), sa.Column("incoterms", sa.String(10)),
        sa.Column("payment_terms", sa.String(200)), sa.Column("valid_until", sa.Date), sa.Column("notes", sa.Text),
        _user("created_by"), _user("verified_by"), _ts("verified_at"),
        _ts("created_at", False, True), _ts("updated_at", False, True),
        sa.CheckConstraint("status IN ('draft','verified')", name="ck_quotes_status"),
        sa.CheckConstraint("source IN ('analysis','manual')", name="ck_quotes_source"),
        sa.CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="ck_quotes_currency"),
        sa.CheckConstraint("status = 'draft' OR verified_by IS NOT NULL", name="ck_quotes_verified_by"),
        sa.UniqueConstraint("rfq_id", "supplier_id", name="uq_rfq_quotes_supplier"),
    )
    op.create_index("ix_rfq_quotes_org", "rfq_quotes", ["organization_id"])

    op.create_table(
        "rfq_quote_lines", _pk(), _org(), _fk("quote_id", "rfq_quotes", cascade=True),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        _fk("rfq_item_id", "rfq_items", nullable=True),
        sa.Column("match_status", sa.String(10), nullable=False, server_default="unmatched"),
        sa.Column("match_score", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("quantity", sa.Float), sa.Column("unit", sa.String(30)),
        sa.Column("unit_price", sa.Float), sa.Column("total_price", sa.Float), sa.Column("lead_time_days", sa.Float),
        sa.Column("confidence", sa.Float, nullable=False, server_default=sa.text("1")),
        sa.Column("source_quote", sa.Text),
        sa.CheckConstraint("match_status IN ('unmatched','suggested','confirmed')", name="ck_quote_lines_match"),
        sa.CheckConstraint("match_status = 'unmatched' OR rfq_item_id IS NOT NULL", name="ck_quote_lines_match_item"),
        sa.CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="ck_quote_lines_price"),
        sa.CheckConstraint("total_price IS NULL OR total_price >= 0", name="ck_quote_lines_total"),
        sa.CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_quote_lines_qty"),
        sa.CheckConstraint("lead_time_days IS NULL OR lead_time_days >= 0", name="ck_quote_lines_lead"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_quote_lines_confidence"),
    )
    op.create_index("ix_rfq_quote_lines_quote", "rfq_quote_lines", ["quote_id", "position"])

    op.create_table(
        "comparisons", _pk(), _org(), _fk("rfq_id", "rfqs", cascade=True), _user("created_by"),
        sa.Column("params", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("inputs_sha256", sa.String(64), nullable=False),
        sa.Column("result", pg.JSONB, nullable=False),
        _ts("created_at", False, True),
    )
    op.create_index("ix_comparisons_rfq", "comparisons", ["rfq_id", "created_at"])

    op.create_table(
        "domain_events", sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True), _org(),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actor_id", pg.UUID(as_uuid=True)),
        sa.Column("from_state", sa.String(24)), sa.Column("to_state", sa.String(24)),
        sa.Column("data", pg.JSONB), sa.Column("note", sa.Text),
        _ts("created_at", False, True),
    )
    op.create_index("ix_domain_events_entity", "domain_events", ["organization_id", "entity_type", "entity_id", "id"])

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING {PREDICATE} WITH CHECK {PREDICATE}")

    for table in ("counters", "supplier_categories", "suppliers", "qualification_criteria",
                  "supplier_qualification_checks", "rfqs", "rfq_suppliers", "rfq_packages", "rfq_quotes"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")
    for table in ("supplier_category_links", "supplier_contacts", "rfq_items", "rfq_quote_lines"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    for table in ("comparisons", "domain_events"):  # append-only for the application
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
