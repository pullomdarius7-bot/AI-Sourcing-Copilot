"""Sprint 6: sourcing agent - conversations, messages, audited runs, reports, full-text knowledge index.

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APP_ROLE = "copilot_app"
PREDICATE = (
    "(organization_id::text = current_setting('app.current_org', true) "
    "OR current_setting('app.system', true) = 'on')"
)
TABLES = ["knowledge_chunks", "agent_conversations", "agent_runs", "agent_messages", "agent_reports"]


def _pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _org() -> sa.Column:
    return sa.Column(
        "organization_id", pg.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def _user(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=nullable)


def _ts(name: str, nullable: bool = True, now: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=sa.text("now()") if now else None)


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks", _pk(), _org(),
        sa.Column("document_id", pg.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_no", sa.Integer, nullable=False),
        sa.Column("char_start", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("tsv", pg.TSVECTOR, sa.Computed("to_tsvector('simple', content)", persisted=True)),
        _ts("created_at", False, True),
        sa.UniqueConstraint("document_id", "chunk_no", name="uq_knowledge_chunks_doc_no"),
    )
    op.create_index("ix_knowledge_chunks_tsv", "knowledge_chunks", ["tsv"], postgresql_using="gin")
    op.create_index("ix_knowledge_chunks_org_doc", "knowledge_chunks", ["organization_id", "document_id"])

    op.create_table(
        "agent_conversations", _pk(), _org(), _user("user_id", nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("context", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _ts("created_at", False, True), _ts("updated_at", False, True),
    )
    op.create_index("ix_agent_conversations_user", "agent_conversations", ["organization_id", "user_id", "updated_at"])

    op.create_table(
        "agent_runs", _pk(), _org(),
        sa.Column("conversation_id", pg.UUID(as_uuid=True), sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False),
        _user("user_id", nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("intent", sa.String(32)),
        sa.Column("prompt_version", sa.String(20), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(40)), sa.Column("model", sa.String(120)),
        sa.Column("plan", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("tool_calls", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("llm_calls", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("safety", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_ids", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("answer_sha256", sa.String(64)),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("input_tokens", sa.Integer), sa.Column("output_tokens", sa.Integer),
        sa.Column("error", sa.Text),
        _ts("created_at", False, True),
        sa.CheckConstraint("mode IN ('deterministic','llm')", name="ck_agent_runs_mode"),
        sa.CheckConstraint("status IN ('answered','fallback','refused','clarify','error')", name="ck_agent_runs_status"),
    )
    op.create_index("ix_agent_runs_org_created", "agent_runs", ["organization_id", "created_at"])
    op.create_index("ix_agent_runs_conversation", "agent_runs", ["conversation_id", "created_at"])

    op.create_table(
        "agent_messages", _pk(), _org(),
        sa.Column("conversation_id", pg.UUID(as_uuid=True), sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("blocks", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("unknowns", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("follow_ups", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sources", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id")),
        sa.Column("feedback", sa.SmallInteger),
        sa.Column("feedback_note", sa.String(1000)),
        _ts("created_at", False, True),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_agent_messages_role"),
        sa.CheckConstraint("feedback IS NULL OR feedback IN (-1, 1)", name="ck_agent_messages_feedback"),
        # an assistant message is always traceable to the run that produced it
        sa.CheckConstraint("role = 'user' OR run_id IS NOT NULL", name="ck_agent_messages_traceable"),
    )
    op.create_index("ix_agent_messages_conversation", "agent_messages", ["conversation_id", "created_at"])

    op.create_table(
        "agent_reports", _pk(), _org(),
        sa.Column("conversation_id", pg.UUID(as_uuid=True), sa.ForeignKey("agent_conversations.id", ondelete="SET NULL")),
        # the report is written by a tool before its run record, in the same transaction
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", deferrable=True, initially="DEFERRED")),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("subject_id", pg.UUID(as_uuid=True)),
        sa.Column("blocks", pg.JSONB, nullable=False),
        sa.Column("unknowns", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("sources", pg.JSONB, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        _user("created_by"), _ts("created_at", False, True),
    )
    op.create_index("ix_agent_reports_org_created", "agent_reports", ["organization_id", "created_at"])

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING {PREDICATE} WITH CHECK {PREDICATE}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON knowledge_chunks TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON agent_conversations TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON agent_messages TO {APP_ROLE}")  # update = feedback only
    for table in ("agent_runs", "agent_reports"):  # append-only audit trail
        op.execute(f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
