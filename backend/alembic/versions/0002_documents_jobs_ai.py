"""Sprint 2: documents, extractions, jobs (queue + dead letter), AI audit trail.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
PREDICATE = (
    "(organization_id::text = current_setting('app.current_org', true) "
    "OR current_setting('app.system', true) = 'on')"
)
NEW_TABLES = ["documents", "document_extractions", "ai_audit_logs", "jobs"]


def _pk() -> sa.Column:
    return sa.Column(
        "id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _org() -> sa.Column:
    return sa.Column(
        "organization_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def _ts(name: str, nullable: bool = True, now: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.text("now()") if now else None,
    )


def upgrade() -> None:
    op.create_table(
        "documents",
        _pk(),
        _org(),
        sa.Column("uploaded_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_backend", sa.String(20)),
        sa.Column("storage_key", sa.String(300)),
        sa.Column("status", sa.String(20), nullable=False, server_default="uploaded"),
        sa.Column("scan_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("scan_engine", sa.String(60)),
        sa.Column("scan_detail", sa.String(200)),
        sa.Column("analysis_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("file_metadata", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("extracted_text", sa.Text),
        sa.Column("error", sa.Text),
        _ts("created_at", False, True),
        _ts("updated_at", False, True),
        _ts("processed_at"),
        sa.CheckConstraint(
            "status IN ('uploaded','scanning','extracting','analyzing','processed','quarantined','failed')",
            name="ck_documents_status",
        ),
    )
    op.create_index("ix_documents_organization_id", "documents", ["organization_id"])
    op.create_index("uq_documents_org_sha256", "documents", ["organization_id", "sha256"], unique=True)
    op.create_index("ix_documents_org_created", "documents", ["organization_id", sa.text("created_at DESC")])

    op.create_table(
        "document_extractions",
        _pk(),
        _org(),
        sa.Column(
            "document_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=False),
        sa.Column("result", pg.JSONB, nullable=False),
        sa.Column("flags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("needs_review", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        _ts("created_at", False, True),
    )
    op.create_index("ix_document_extractions_organization_id", "document_extractions", ["organization_id"])
    op.create_index("ix_document_extractions_document_id", "document_extractions", ["document_id"])

    op.create_table(
        "ai_audit_logs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        _org(),
        sa.Column("document_id", pg.UUID(as_uuid=True)),
        sa.Column("user_id", pg.UUID(as_uuid=True)),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("purpose", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("response_sha256", sa.String(64)),
        sa.Column("input_chars", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("flags", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.String(500)),
        _ts("created_at", False, True),
    )
    op.create_index("ix_ai_audit_logs_org_id_id", "ai_audit_logs", ["organization_id", sa.text("id DESC")])
    op.create_index("ix_ai_audit_logs_document_id", "ai_audit_logs", ["document_id"])

    op.create_table(
        "jobs",
        _pk(),
        _org(),
        sa.Column("queue", sa.String(40), nullable=False, server_default="default"),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("dedupe_key", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default=sa.text("5")),
        _ts("run_at", False, True),
        sa.Column("locked_by", sa.String(80)),
        _ts("locked_until"),
        sa.Column("last_error", sa.Text),
        sa.Column("result", pg.JSONB),
        _ts("created_at", False, True),
        _ts("started_at"),
        _ts("finished_at"),
        sa.CheckConstraint("status IN ('queued','running','succeeded','dead')", name="ck_jobs_status"),
    )
    op.create_index("ix_jobs_organization_id", "jobs", ["organization_id"])
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_at"])
    op.create_index(
        "uq_jobs_active_dedupe", "jobs", ["dedupe_key"], unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL AND status IN ('queued','running')"),
    )

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING {PREDICATE} WITH CHECK {PREDICATE}"
        )

    for table in ("documents", "document_extractions", "jobs"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON ai_audit_logs TO {APP_ROLE}")  # append-only


def downgrade() -> None:
    for table in ("jobs", "ai_audit_logs", "document_extractions", "documents"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
