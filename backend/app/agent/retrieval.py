"""Retrieval layer: PostgreSQL full-text search over document text, plus fuzzy entity lookups.

No external vector store or embedding service: the index is a generated tsvector column with a GIN index, so search
works the moment the stack is up. Documents are indexed by the worker when processing finishes, and on demand during
a search for anything processed before this feature existed.
"""
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.text import RFQ_NUMBER, best_excerpt, chunk_text, query_terms
from app.config import get_settings
from app.models import Document, KnowledgeChunk, Rfq, Supplier

settings = get_settings()
__all__ = ["RFQ_NUMBER", "best_excerpt", "chunk_text", "query_terms"]


# --- Indexing -----------------------------------------------------------------------------------------------------------
async def index_document(db: AsyncSession, doc: Document) -> int:
    """(Re)build the chunks of one processed document. Caller commits."""
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
    if doc.status != "processed" or doc.scan_status != "clean" or not (doc.extracted_text or "").strip():
        return 0
    pieces = chunk_text(doc.extracted_text or "", settings.agent_index_chunk_chars)
    for c in pieces:
        db.add(KnowledgeChunk(organization_id=doc.organization_id, document_id=doc.id, chunk_no=c.no,
                              char_start=c.start, content=c.text))
    await db.flush()
    return len(pieces)


async def ensure_indexed(db: AsyncSession, org_id: uuid.UUID, limit: int | None = None) -> int:
    """Index processed documents that have no chunks yet (bounded). Returns the number of documents indexed."""
    limit = settings.agent_index_on_demand_docs if limit is None else limit
    if limit <= 0:
        return 0
    has_chunks = select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == Document.id).exists()
    docs = list(await db.scalars(
        select(Document).where(Document.organization_id == org_id, Document.status == "processed",
                               Document.scan_status == "clean", Document.extracted_text.is_not(None), ~has_chunks)
        .order_by(Document.created_at).limit(limit)
    ))
    for doc in docs:
        await index_document(db, doc)
    return len(docs)


# --- Search ---------------------------------------------------------------------------------------------------------------
@dataclass
class Hit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    chunk_no: int
    content: str
    rank: float


async def search_chunks(db: AsyncSession, org_id: uuid.UUID, question: str, limit: int = 5,
                        document_id: uuid.UUID | None = None) -> list[Hit]:
    lexemes = sorted({_ts_lexeme(t) for t in query_terms(question)} - {""})
    if not lexemes:
        return []
    # OR of the terms, ranked: a chunk matching more terms ranks higher. Terms are passed as data, never as SQL.
    tsquery = func.to_tsquery("simple", " | ".join(lexemes))
    tsv = literal_column("knowledge_chunks.tsv")
    rank = func.ts_rank_cd(tsv, tsquery).label("rank")
    stmt = (
        select(KnowledgeChunk.id, KnowledgeChunk.document_id, Document.filename, KnowledgeChunk.chunk_no,
               KnowledgeChunk.content, rank)
        .join(Document, Document.id == KnowledgeChunk.document_id)
        .where(KnowledgeChunk.organization_id == org_id, tsv.op("@@")(tsquery))
        .order_by(rank.desc(), KnowledgeChunk.document_id, KnowledgeChunk.chunk_no)
        .limit(limit)
    )
    if document_id is not None:
        stmt = stmt.where(KnowledgeChunk.document_id == document_id)
    rows = (await db.execute(stmt)).all()
    return [Hit(r[0], r[1], r[2], r[3], r[4], float(r[5])) for r in rows]


def _ts_lexeme(term: str) -> str:
    """A term as a safe tsquery lexeme (letters and digits only, prefix match for longer words)."""
    clean = re.sub(r"[^\w]", "", term, flags=re.UNICODE)
    if not clean:
        return ""
    return f"{clean}:*" if len(clean) >= 4 and not clean.isdigit() else clean


# --- Entity lookups ----------------------------------------------------------------------------------------------------------
async def find_rfqs(db: AsyncSession, org_id: uuid.UUID, reference: str, limit: int = 5) -> list[Rfq]:
    ref = reference.strip()
    try:
        rid = uuid.UUID(ref)
        row = await db.scalar(select(Rfq).where(Rfq.id == rid, Rfq.organization_id == org_id))
        return [row] if row else []
    except ValueError:
        pass
    numbers = RFQ_NUMBER.findall(ref)
    if numbers:
        rows = await db.scalars(select(Rfq).where(Rfq.organization_id == org_id,
                                                 func.upper(Rfq.number).in_([n.upper() for n in numbers])))
        return list(rows)
    pattern = f"%{_like(ref)}%"
    rows = await db.scalars(select(Rfq).where(Rfq.organization_id == org_id,
                                             or_(Rfq.title.ilike(pattern, escape="\\"), Rfq.number.ilike(pattern, escape="\\")))
                           .order_by(Rfq.created_at.desc()).limit(limit))
    return list(rows)


async def find_suppliers(db: AsyncSession, org_id: uuid.UUID, reference: str, limit: int = 5) -> list[Supplier]:
    ref = reference.strip()
    try:
        sid = uuid.UUID(ref)
        row = await db.scalar(select(Supplier).where(Supplier.id == sid, Supplier.organization_id == org_id))
        return [row] if row else []
    except ValueError:
        pass
    pattern = f"%{_like(ref)}%"
    rows = await db.scalars(select(Supplier).where(Supplier.organization_id == org_id, or_(
        Supplier.name.ilike(pattern, escape="\\"), Supplier.code.ilike(pattern, escape="\\"),
        Supplier.legal_name.ilike(pattern, escape="\\"))).order_by(Supplier.name).limit(limit))
    return list(rows)


async def find_documents(db: AsyncSession, org_id: uuid.UUID, reference: str, limit: int = 5) -> list[Document]:
    ref = reference.strip()
    try:
        did = uuid.UUID(ref)
        row = await db.scalar(select(Document).where(Document.id == did, Document.organization_id == org_id))
        return [row] if row else []
    except ValueError:
        pass
    pattern = f"%{_like(ref)}%"
    rows = await db.scalars(select(Document).where(Document.organization_id == org_id,
                                                  Document.filename.ilike(pattern, escape="\\"))
                           .order_by(Document.created_at.desc()).limit(limit))
    return list(rows)


async def mentioned_suppliers(db: AsyncSession, org_id: uuid.UUID, question: str, limit: int = 3) -> list[Supplier]:
    """Suppliers whose name or code appears in the question (whole words, case-insensitive)."""
    q = f" {_words(question)} "
    rows = (await db.execute(select(Supplier).where(Supplier.organization_id == org_id).order_by(Supplier.name).limit(2000))).scalars()
    found = []
    for s in rows:
        names = {_words(s.name), _words(s.code)} | ({_words(s.legal_name)} if s.legal_name else set())
        if any(n and f" {n} " in q for n in names):
            found.append(s)
    found.sort(key=lambda s: -len(s.name))
    return found[:limit]


async def mentioned_documents(db: AsyncSession, org_id: uuid.UUID, question: str, limit: int = 2) -> list[Document]:
    low = question.lower()
    rows = (await db.execute(select(Document.id, Document.filename).where(Document.organization_id == org_id)
                             .order_by(Document.created_at.desc()).limit(2000))).all()
    hits = []
    for doc_id, filename in rows:
        stem = filename.rsplit(".", 1)[0].lower()
        if filename.lower() in low or (len(stem) >= 4 and stem in low):
            hits.append(doc_id)
    if not hits:
        return []
    return list(await db.scalars(select(Document).where(Document.id.in_(hits[:limit]))))


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:200]


def _words(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.lower()))

