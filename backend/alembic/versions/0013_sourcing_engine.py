"""AI sourcing engine (buyer requests, marketplace + web supplier discovery, supplier invites) and the features the
pricing page promises (private buyer manifests, margin rules, export API keys, priority processing).

Row-level security:
- sourcing_requests: the buyer; every account may read a request while it is open to suppliers (the lead feed).
- sourcing_candidates, api_keys: the owning account only.
- supplier_invites, email_suppressions: platform records, readable and writable only by the system.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
SYSTEM = "current_setting('app.system', true) = 'on'"
TENANT = f"(organization_id::text = current_setting('app.current_org', true) OR {SYSTEM})"


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _org() -> sa.Column:
    return sa.Column("organization_id", pg.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                     nullable=False)


def _ts(name: str, now: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=not now,
                     server_default=sa.text("now()") if now else None)


def _rls(table: str, predicate: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING {predicate} WITH CHECK {predicate}")


def upgrade() -> None:
    # --- Pricing-page features -------------------------------------------------------------------------
    op.add_column("organizations", sa.Column("analysis_defaults", pg.JSONB, nullable=False,
                                             server_default=sa.text("'{}'::jsonb")))
    op.add_column("jobs", sa.Column("priority", sa.SmallInteger, nullable=False, server_default=sa.text("0")))
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.create_index("ix_jobs_claim", "jobs", ["status", sa.text("priority DESC"), "run_at"])
    # A buyer's own manifest (e.g. from an outside auction) is a private lot: analysed, never listed.
    op.add_column("marketplace_lots", sa.Column("private", sa.Boolean, nullable=False, server_default=sa.text("false")))
    op.add_column("marketplace_lots", sa.Column("auto_analysis", pg.JSONB))
    op.create_check_constraint("ck_lots_private_unlisted", "marketplace_lots", "NOT private OR status = 'draft'")

    op.create_table(
        "api_keys",
        _uuid_pk(), _org(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        _ts("created_at", now=True), _ts("last_used_at"), _ts("revoked_at"),
    )
    op.create_index("ix_api_keys_org", "api_keys", ["organization_id"])
    _rls("api_keys", TENANT)

    # --- Sourcing requests ----------------------------------------------------------------------------
    op.create_table(
        "sourcing_requests",
        _uuid_pk(), _org(),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("category", sa.String(20)),
        sa.Column("condition", sa.String(20)),
        sa.Column("quantity", sa.Integer),
        sa.Column("target_unit_price", sa.Numeric(12, 2)),
        sa.Column("region", sa.String(12)),
        sa.Column("needed_by", sa.Date),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("open_to_suppliers", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("search_web", sa.String(8), nullable=False, server_default="auto"),
        sa.Column("web_status", sa.String(10), nullable=False, server_default="none"),
        sa.Column("web_note", sa.Text),
        sa.Column("marketplace_matches", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("web_matches", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("invites_sent", sa.Integer, nullable=False, server_default=sa.text("0")),
        _ts("created_at", now=True), _ts("completed_at"), _ts("closed_at"),
        sa.CheckConstraint("status IN ('open','closed')", name="ck_sourcing_requests_status"),
        sa.CheckConstraint("search_web IN ('auto','always','never')", name="ck_sourcing_requests_search_web"),
        sa.CheckConstraint("web_status IN ('none','queued','running','done','skipped','failed')",
                           name="ck_sourcing_requests_web_status"),
    )
    op.create_index("ix_sourcing_requests_org", "sourcing_requests", ["organization_id", sa.text("created_at DESC")])
    op.create_index("ix_sourcing_requests_leads", "sourcing_requests", ["status", "open_to_suppliers", "category"])
    op.execute("ALTER TABLE sourcing_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sourcing_requests FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY lead_read ON sourcing_requests FOR SELECT USING (status = 'open' AND open_to_suppliers)")
    op.execute(f"CREATE POLICY tenant_isolation ON sourcing_requests USING {TENANT} WITH CHECK {TENANT}")

    op.create_table(
        "sourcing_candidates",
        _uuid_pk(), _org(),
        sa.Column("request_id", pg.UUID(as_uuid=True), sa.ForeignKey("sourcing_requests.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("lot_id", pg.UUID(as_uuid=True), sa.ForeignKey("marketplace_lots.id", ondelete="SET NULL")),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("website", sa.String(500)),
        sa.Column("domain", sa.String(255)),
        sa.Column("business_type", sa.String(20)),
        sa.Column("location", sa.String(200)),
        sa.Column("summary", sa.Text),
        sa.Column("categories", pg.ARRAY(sa.String(20)), nullable=False, server_default="{}"),
        sa.Column("products", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("moq", sa.String(200)),
        sa.Column("price_notes", sa.String(300)),
        sa.Column("contact_email", sa.String(320)),
        sa.Column("contact_phone", sa.String(60)),
        sa.Column("contact_url", sa.String(500)),
        sa.Column("relevance", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("method", sa.String(8), nullable=False, server_default="rules"),
        sa.Column("status", sa.String(10), nullable=False, server_default="new"),
        sa.Column("invite_status", sa.String(12)),
        sa.Column("supplier_id", pg.UUID(as_uuid=True), sa.ForeignKey("suppliers.id", ondelete="SET NULL")),
        sa.Column("flags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        _ts("created_at", now=True),
        sa.CheckConstraint("kind IN ('marketplace','web')", name="ck_sourcing_candidates_kind"),
        sa.CheckConstraint("status IN ('new','added','dismissed')", name="ck_sourcing_candidates_status"),
    )
    op.create_index("ix_sourcing_candidates_request", "sourcing_candidates", ["request_id", sa.text("relevance DESC")])
    _rls("sourcing_candidates", TENANT)

    op.add_column("rfq_threads", sa.Column(
        "request_id", pg.UUID(as_uuid=True), sa.ForeignKey("sourcing_requests.id", ondelete="SET NULL")))

    # --- Supplier invites (platform-level) ------------------------------------------------------------
    op.create_table(
        "supplier_invites",
        _uuid_pk(),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("status_note", sa.String(300)),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("demand", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("request_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("sent_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        _ts("last_sent_at"),
        sa.Column("joined_org_id", pg.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="SET NULL")),
        _ts("created_at", now=True), _ts("updated_at", now=True),
        sa.CheckConstraint("status IN ('pending','sent','held','suppressed','joined','failed')",
                           name="ck_supplier_invites_status"),
    )
    op.create_table(
        "email_suppressions",
        sa.Column("value", sa.String(320), primary_key=True),  # an email address or a whole domain
        sa.Column("reason", sa.String(40), nullable=False),
        _ts("created_at", now=True),
    )
    for table in ("supplier_invites", "email_suppressions"):
        _rls(table, f"({SYSTEM})")

    grants = {
        "api_keys": "SELECT, INSERT, UPDATE",
        "sourcing_requests": "SELECT, INSERT, UPDATE",
        "sourcing_candidates": "SELECT, INSERT, UPDATE",
        "supplier_invites": "SELECT, INSERT, UPDATE",
        "email_suppressions": "SELECT, INSERT",
    }
    for table, rights in grants.items():
        op.execute(f"GRANT {rights} ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_column("rfq_threads", "request_id")
    for table in ("email_suppressions", "supplier_invites", "sourcing_candidates", "sourcing_requests", "api_keys"):
        op.drop_table(table)
    op.drop_constraint("ck_lots_private_unlisted", "marketplace_lots", type_="check")
    op.drop_column("marketplace_lots", "auto_analysis")
    op.drop_column("marketplace_lots", "private")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_at"])
    op.drop_column("jobs", "priority")
    op.drop_column("organizations", "analysis_defaults")
