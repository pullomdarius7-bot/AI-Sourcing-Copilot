"""Two-sided marketplace: supplier lots, normalized manifest lines, buyer analyses and masked RFQ threads.

Visibility is enforced by row-level security, not only by the API:

- marketplace_lots / lot_items: the owning supplier sees everything; every other account sees a lot and its
  lines only while it is published (or sold). These rows hold no contact details.
- lot_private_details: reserve price and pickup address. Owner only, like every tenant table.
- market_profiles: the masked public face of an account (alias, badges). Readable by all, written only by the
  account itself or the system.
- rfq_threads / rfq_thread_events: readable and writable only by the two parties of the thread. Events are
  append-only and hash-chained so the negotiation history can serve as evidence for escrow.
- rfq_moderation: the original text of a message that had contact details removed. Only the author's account
  can read it.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
SYSTEM = "current_setting('app.system', true) = 'on'"
CURRENT = "current_setting('app.current_org', true)"
TENANT = f"(organization_id::text = {CURRENT} OR {SYSTEM})"
PARTY = f"(buyer_org_id::text = {CURRENT} OR supplier_org_id::text = {CURRENT} OR {SYSTEM})"
LISTED = "('published','sold')"


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _org(name: str = "organization_id", nullable: bool = False) -> sa.Column:
    return sa.Column(name, pg.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=nullable)


def _ts(name: str, default_now: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=not default_now,
        server_default=sa.text("now()") if default_now else None,
    )


def _money(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(14, 2))


def _force_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.add_column("documents", sa.Column("purpose", sa.String(12), nullable=False, server_default="sourcing"))
    op.create_check_constraint("ck_documents_purpose", "documents", "purpose IN ('sourcing','manifest')")

    op.create_table(
        "market_profiles",
        sa.Column("organization_id", pg.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("alias", sa.String(24), nullable=False, unique=True),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("label", sa.String(40), nullable=False),
        sa.Column("hub_region", sa.String(12)),
        sa.Column("verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("completed_deals", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("member_since", sa.DateTime(timezone=True), nullable=False),
        _ts("updated_at", default_now=True),
        sa.CheckConstraint("side IN ('buyer','seller')", name="ck_market_profiles_side"),
    )

    op.create_table(
        "marketplace_lots",
        _uuid_pk(),
        _org(),
        sa.Column("document_id", pg.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("default_condition", sa.String(20)),
        sa.Column("ingest_status", sa.String(12), nullable=False, server_default="processing"),
        sa.Column("ingest_method", sa.String(8)),
        sa.Column("ingest_error", sa.Text),
        sa.Column("ingest_flags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(12), nullable=False, server_default="draft"),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("description", sa.Text),
        sa.Column("lot_type", sa.String(14)),
        sa.Column("pallet_count", sa.Integer),
        _money("asking_price"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("dispatch_zip", sa.String(5)),
        sa.Column("region", sa.String(12)),
        sa.Column("liftgate_required", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("dock_high_only", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("local_pickup", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("item_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("total_units", sa.Integer, nullable=False, server_default=sa.text("0")),
        _money("total_msrp"),
        _money("est_resale"),
        sa.Column("est_margin_pct", sa.Numeric(7, 2)),
        sa.Column("primary_category", sa.String(20)),
        sa.Column("categories", pg.ARRAY(sa.String(20)), nullable=False, server_default="{}"),
        sa.Column("conditions", pg.ARRAY(sa.String(20)), nullable=False, server_default="{}"),
        sa.Column("condition_mix", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("category_mix", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("published_at"),
        _ts("sold_at"),
        _ts("created_at", default_now=True),
        _ts("updated_at", default_now=True),
        sa.CheckConstraint("ingest_status IN ('processing','ready','failed')", name="ck_lots_ingest_status"),
        sa.CheckConstraint("status IN ('draft','published','sold','withdrawn')", name="ck_lots_status"),
        sa.CheckConstraint("lot_type IS NULL OR lot_type IN ('single_pallet','multi_pallet','truckload')", name="ck_lots_type"),
        sa.CheckConstraint("asking_price IS NULL OR asking_price > 0", name="ck_lots_asking"),
    )
    op.create_index("ix_marketplace_lots_org", "marketplace_lots", ["organization_id"])
    op.create_index("ix_marketplace_lots_listed", "marketplace_lots", ["status", sa.text("published_at DESC")])
    op.create_index("ix_marketplace_lots_document", "marketplace_lots", ["document_id"])

    op.create_table(
        "lot_items",
        _uuid_pk(),
        _org(),
        sa.Column("lot_id", pg.UUID(as_uuid=True), sa.ForeignKey("marketplace_lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("source_line", sa.Integer),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("brand", sa.String(120)),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("condition", sa.String(20), nullable=False),
        sa.Column("upc", sa.String(14)),
        sa.Column("asin", sa.String(10)),
        sa.Column("model_number", sa.String(60)),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("unit_msrp", sa.Numeric(12, 2)),
        sa.Column("unit_price", sa.Numeric(12, 2)),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("flags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.CheckConstraint("quantity > 0", name="ck_lot_items_quantity"),
    )
    op.create_index("ix_lot_items_lot", "lot_items", ["lot_id", "position"])

    op.create_table(
        "lot_private_details",
        sa.Column("lot_id", pg.UUID(as_uuid=True), sa.ForeignKey("marketplace_lots.id", ondelete="CASCADE"), primary_key=True),
        _org(),
        _money("reserve_price"),
        sa.Column("pickup_address", sa.Text),
        sa.Column("pickup_contact", sa.String(200)),
        sa.Column("pickup_phone", sa.String(40)),
        sa.Column("pickup_notes", sa.Text),
        _ts("updated_at", default_now=True),
    )

    op.create_table(
        "lot_analyses",
        _uuid_pk(),
        _org(),
        sa.Column("lot_id", pg.UUID(as_uuid=True), sa.ForeignKey("marketplace_lots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(10), nullable=False, server_default="queued"),
        sa.Column("params", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", pg.JSONB),
        sa.Column("method", sa.String(8)),
        sa.Column("error", sa.Text),
        _ts("created_at", default_now=True),
        _ts("completed_at"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed')", name="ck_lot_analyses_status"),
    )
    op.create_index("ix_lot_analyses_org_lot", "lot_analyses", ["organization_id", "lot_id", sa.text("created_at DESC")])

    op.create_table(
        "rfq_threads",
        _uuid_pk(),
        sa.Column("lot_id", pg.UUID(as_uuid=True), sa.ForeignKey("marketplace_lots.id", ondelete="CASCADE"), nullable=False),
        _org("buyer_org_id"),
        _org("supplier_org_id"),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("lot_title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("closed_reason", sa.String(40)),
        sa.Column("offer_by", sa.String(8)),
        _money("offer_price"),
        sa.Column("shipping_terms", sa.String(16)),
        sa.Column("target_close_date", sa.Date),
        sa.Column("custom_terms", sa.Text),
        _money("agreed_price"),
        sa.Column("revealed", pg.JSONB),
        _ts("accepted_at"),
        _ts("last_event_at", default_now=True),
        _ts("created_at", default_now=True),
        sa.CheckConstraint("status IN ('open','accepted','declined','withdrawn','closed')", name="ck_rfq_threads_status"),
        sa.CheckConstraint("buyer_org_id <> supplier_org_id", name="ck_rfq_threads_parties"),
        sa.CheckConstraint("shipping_terms IS NULL OR shipping_terms IN ('buyer_freight','seller_freight')",
                           name="ck_rfq_threads_shipping"),
    )
    op.create_index("ix_rfq_threads_buyer", "rfq_threads", ["buyer_org_id", sa.text("last_event_at DESC")])
    op.create_index("ix_rfq_threads_supplier", "rfq_threads", ["supplier_org_id", sa.text("last_event_at DESC")])
    op.create_index("ix_rfq_threads_lot", "rfq_threads", ["lot_id"])
    op.create_index("uq_rfq_threads_open_per_buyer", "rfq_threads", ["lot_id", "buyer_org_id"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))

    op.create_table(
        "rfq_thread_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("thread_id", pg.UUID(as_uuid=True), sa.ForeignKey("rfq_threads.id", ondelete="CASCADE"), nullable=False),
        _org("buyer_org_id"),
        _org("supplier_org_id"),
        sa.Column("actor_side", sa.String(8), nullable=False),
        sa.Column("actor_user_id", pg.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(14), nullable=False),
        sa.Column("offer", pg.JSONB),
        sa.Column("body", sa.Text),
        sa.Column("flags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("prev_hash", sa.String(64)),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("actor_side IN ('buyer','supplier','system')", name="ck_rfq_events_side"),
        sa.CheckConstraint(
            "kind IN ('offer','counter','accept','decline','withdraw','message','auto_decline','closed')",
            name="ck_rfq_events_kind",
        ),
    )
    op.create_index("ix_rfq_thread_events_thread", "rfq_thread_events", ["thread_id", "id"])

    op.create_table(
        "rfq_moderation",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        _org(),
        sa.Column("thread_id", pg.UUID(as_uuid=True), sa.ForeignKey("rfq_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.BigInteger, nullable=False),
        sa.Column("author_id", pg.UUID(as_uuid=True)),
        sa.Column("original", sa.Text, nullable=False),
        sa.Column("flags", pg.JSONB, nullable=False),
        _ts("created_at", default_now=True),
    )

    # --- Row-level security --------------------------------------------------------------------------
    for table in ("lot_private_details", "lot_analyses", "rfq_moderation"):
        _force_rls(table)
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING {TENANT} WITH CHECK {TENANT}")

    _force_rls("market_profiles")
    op.execute("CREATE POLICY public_read ON market_profiles FOR SELECT USING (true)")
    op.execute(f"CREATE POLICY own_write ON market_profiles USING {TENANT} WITH CHECK {TENANT}")

    _force_rls("marketplace_lots")
    op.execute(f"CREATE POLICY listed_read ON marketplace_lots FOR SELECT USING (status IN {LISTED})")
    op.execute(f"CREATE POLICY tenant_isolation ON marketplace_lots USING {TENANT} WITH CHECK {TENANT}")

    _force_rls("lot_items")
    op.execute(
        "CREATE POLICY listed_read ON lot_items FOR SELECT USING (EXISTS (SELECT 1 FROM marketplace_lots l "
        f"WHERE l.id = lot_items.lot_id AND l.status IN {LISTED}))"
    )
    op.execute(f"CREATE POLICY tenant_isolation ON lot_items USING {TENANT} WITH CHECK {TENANT}")

    for table in ("rfq_threads", "rfq_thread_events"):
        _force_rls(table)
        op.execute(f"CREATE POLICY party_access ON {table} USING {PARTY} WITH CHECK {PARTY}")

    # --- Grants ---------------------------------------------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON market_profiles TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON marketplace_lots TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON lot_items TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON lot_private_details TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON lot_analyses TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON rfq_threads TO {APP_ROLE}")
    # The negotiation record is append-only for the application.
    op.execute(f"GRANT SELECT, INSERT ON rfq_thread_events TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON rfq_moderation TO {APP_ROLE}")


def downgrade() -> None:
    for table in ("rfq_moderation", "rfq_thread_events", "rfq_threads", "lot_analyses", "lot_private_details",
                  "lot_items", "marketplace_lots", "market_profiles"):
        op.drop_table(table)
    op.drop_constraint("ck_documents_purpose", "documents", type_="check")
    op.drop_column("documents", "purpose")
