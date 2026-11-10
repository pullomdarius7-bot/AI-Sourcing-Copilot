"""Sprint 3: extraction runs, requirements, parameters, review history.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
PREDICATE = (
    "(organization_id::text = current_setting('app.current_org', true) "
    "OR current_setting('app.system', true) = 'on')"
)
STATES = "('unverified','verified','corrected','rejected')"


def _pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _org() -> sa.Column:
    return sa.Column(
        "organization_id", pg.UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
    )


def _ts(name: str, nullable: bool = True, now: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.text("now()") if now else None,
    )


def _evidence_columns() -> list[sa.Column]:
    return [
        sa.Column("source_quote", sa.Text),
        sa.Column("source_start", sa.Integer),
        sa.Column("source_end", sa.Integer),
        sa.Column("source_line", sa.Integer),
        sa.Column("source_section", sa.String(200)),
        sa.Column("model_confidence", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("confidence", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("confidence_detail", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("grounding_status", sa.String(12), nullable=False, server_default="grounded"),
        sa.Column("flags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("validation_state", sa.String(12), nullable=False, server_default="unverified"),
        sa.Column("verified_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        _ts("verified_at"),
        sa.Column("review_note", sa.Text),
        sa.Column("original", pg.JSONB),
        _ts("created_at", False, True),
        _ts("updated_at", False, True),
    ]


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        _pk(),
        _org(),
        sa.Column("document_id", pg.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="extracting"),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("provider", sa.String(40)),
        sa.Column("model", sa.String(120)),
        sa.Column("prompt_version", sa.String(20)),
        sa.Column("source_text_sha256", sa.String(64)),
        sa.Column("flags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.Text),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("approved_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        _ts("approved_at"),
        sa.Column("decision_note", sa.Text),
        _ts("created_at", False, True),
        _ts("updated_at", False, True),
        _ts("completed_at"),
        sa.CheckConstraint(
            "status IN ('extracting','pending_review','ready_for_approval','approved','rejected','failed','superseded')",
            name="ck_runs_status",
        ),
    )
    op.create_index("ix_extraction_runs_organization_id", "extraction_runs", ["organization_id"])
    op.create_index("ix_extraction_runs_document_id", "extraction_runs", ["document_id"])

    op.create_table(
        "requirements",
        _pk(),
        _org(),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("extraction_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("statement", sa.Text, nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="other"),
        sa.Column("priority", sa.String(12), nullable=False, server_default="unknown"),
        *_evidence_columns(),
        sa.CheckConstraint(f"validation_state IN {STATES}", name="ck_requirements_state"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_requirements_confidence"),
    )
    op.create_index("ix_requirements_organization_id", "requirements", ["organization_id"])
    op.create_index("ix_requirements_run_id", "requirements", ["run_id"])

    op.create_table(
        "parameters",
        _pk(),
        _org(),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("extraction_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", pg.UUID(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="CASCADE")),
        sa.Column("document_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("value_type", sa.String(8), nullable=False, server_default="text"),
        sa.Column("value_text", sa.Text),
        sa.Column("value_number", sa.Float),
        sa.Column("unit", sa.String(30)),
        sa.Column("qualifier", sa.String(8)),
        sa.Column("unknown", sa.Boolean, nullable=False, server_default=sa.text("true")),
        *_evidence_columns(),
        sa.CheckConstraint(f"validation_state IN {STATES}", name="ck_parameters_state"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_parameters_confidence"),
        # unknown stays unknown: an unknown parameter can never carry a value
        sa.CheckConstraint(
            "NOT unknown OR (value_text IS NULL AND value_number IS NULL)", name="ck_parameters_unknown_has_no_value"
        ),
        # a known, machine-extracted value must point at its evidence
        sa.CheckConstraint(
            "unknown OR grounding_status = 'manual' OR (source_quote IS NOT NULL AND source_start IS NOT NULL AND source_end IS NOT NULL)",
            name="ck_parameters_known_has_source",
        ),
    )
    op.create_index("ix_parameters_organization_id", "parameters", ["organization_id"])
    op.create_index("ix_parameters_run_id", "parameters", ["run_id"])
    op.create_index("ix_parameters_requirement_id", "parameters", ["requirement_id"])
    op.create_index("ix_parameters_review", "parameters", ["organization_id", "validation_state", "confidence"])

    op.create_table(
        "review_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        _org(),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_type", sa.String(12), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("actor_id", pg.UUID(as_uuid=True)),
        sa.Column("from_state", sa.String(24)),
        sa.Column("to_state", sa.String(24)),
        sa.Column("before", pg.JSONB),
        sa.Column("after", pg.JSONB),
        sa.Column("note", sa.Text),
        _ts("created_at", False, True),
    )
    op.create_index("ix_review_events_org_run", "review_events", ["organization_id", "run_id", "id"])
    op.create_index("ix_review_events_item", "review_events", ["item_id"])

    for table in ("extraction_runs", "requirements", "parameters", "review_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING {PREDICATE} WITH CHECK {PREDICATE}")

    for table in ("extraction_runs", "requirements", "parameters"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON review_events TO {APP_ROLE}")  # append-only history


def downgrade() -> None:
    for table in ("review_events", "parameters", "requirements", "extraction_runs"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
