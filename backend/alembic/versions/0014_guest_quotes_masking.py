"""Free guest quotes from invited suppliers, masked web-supplier identities, and the invited-supplier promotion.

- guest_quotes: a quote an invited supplier sends without an account. It belongs to the buyer's account (the buyer
  reads it); suppliers write it through the system. Continuing the conversation needs a free registration, which
  turns the quote into a normal masked negotiation.
- sourcing_candidates no longer hold a web supplier's identity. Name, website, email and phone stay in the
  platform-only supplier_invites table; the buyer's row keeps a masked profile and a hash of the domain (to avoid
  reading the same site twice). Existing rows are masked in place; this cannot be undone by the downgrade.
- organizations.signup_promo: 'invite_first_month' for suppliers who joined through an invitation, 'used' once
  the discounted first month has been bought.
- rfq_threads.lot_id becomes optional: a negotiation can start from a quote on a buyer request.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
TENANT = ("(organization_id::text = current_setting('app.current_org', true) "
          "OR current_setting('app.system', true) = 'on')")


def upgrade() -> None:
    op.add_column("organizations", sa.Column("signup_promo", sa.String(24)))
    op.alter_column("rfq_threads", "lot_id", nullable=True)

    op.add_column("sourcing_candidates", sa.Column("domain_hash", sa.String(64)))
    op.add_column("sourcing_candidates", sa.Column(
        "invite_id", pg.UUID(as_uuid=True), sa.ForeignKey("supplier_invites.id", ondelete="SET NULL")))
    op.execute("SELECT set_config('app.system', 'on', true)")
    op.execute("""
        UPDATE sourcing_candidates
           SET domain_hash = encode(sha256(convert_to(lower(domain), 'UTF8')), 'hex'),
               company_name = 'Web supplier', website = NULL, domain = NULL, contact_email = NULL,
               contact_phone = NULL, contact_url = NULL, supplier_id = NULL
         WHERE kind = 'web'
    """)

    op.create_table(
        "guest_quotes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", pg.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("request_id", pg.UUID(as_uuid=True), sa.ForeignKey("sourcing_requests.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("invite_id", pg.UUID(as_uuid=True), sa.ForeignKey("supplier_invites.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("supplier_alias", sa.String(24), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("quantity_available", sa.Integer, nullable=False),
        sa.Column("condition", sa.String(20), nullable=False),
        sa.Column("available_by", sa.Date, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("flags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(12), nullable=False, server_default="new"),
        sa.Column("buyer_reply", sa.Text),
        sa.Column("thread_id", pg.UUID(as_uuid=True), sa.ForeignKey("rfq_threads.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('new','interested','declined','converted')", name="ck_guest_quotes_status"),
        sa.CheckConstraint("unit_price > 0 AND quantity_available > 0", name="ck_guest_quotes_amounts"),
    )
    op.create_index("uq_guest_quotes_invite_request", "guest_quotes", ["invite_id", "request_id"], unique=True)
    op.create_index("ix_guest_quotes_request", "guest_quotes", ["request_id"])
    op.execute("ALTER TABLE guest_quotes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE guest_quotes FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON guest_quotes USING {TENANT} WITH CHECK {TENANT}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON guest_quotes TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("guest_quotes")
    op.drop_column("sourcing_candidates", "invite_id")
    op.drop_column("sourcing_candidates", "domain_hash")
    op.execute("DELETE FROM rfq_threads WHERE lot_id IS NULL")
    op.alter_column("rfq_threads", "lot_id", nullable=False)
    op.drop_column("organizations", "signup_promo")
