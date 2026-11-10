"""Pure text helpers shared by retrieval, the planner and the tools (no database, no I/O)."""
import re
from dataclasses import dataclass

STOPWORDS = frozenset(
    "a an and are as at be by can could did do does for from has have how i in is it its me my of on or our please "
    "show tell that the their this to us was we what when where which who why will with you your about give list "
    "find any all there".split()
)
RFQ_NUMBER = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{4}-\d{3,6}\b", re.IGNORECASE)
SUPPLIER_CODE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{3,6}\b", re.IGNORECASE)


# --- Chunking (pure) ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Chunk:
    no: int
    start: int
    text: str


def chunk_text(text_value: str, size: int = 1500, overlap: int = 200) -> list[Chunk]:
    """Split on paragraph, then sentence, then word boundaries near `size`; consecutive chunks overlap."""
    content = text_value.replace("\r\n", "\n").replace("\x00", "")
    if not content.strip():
        return []
    chunks: list[Chunk] = []
    start, n = 0, 0
    length = len(content)
    while start < length:
        end = min(start + size, length)
        if end < length:
            window = content[start:end]
            cut = max(window.rfind("\n\n"), -1)
            if cut < size // 2:
                cut = max(window.rfind(". "), window.rfind("\n"))
            if cut < size // 2:
                cut = window.rfind(" ")
            if cut >= size // 2:
                end = start + cut + 1
        piece = content[start:end].strip()
        if piece:
            chunks.append(Chunk(n, start, piece))
            n += 1
        if end >= length:
            break
        start = max(end - overlap, start + 1)
        while start < length and not content[start - 1].isspace() and start < end:
            start += 1  # begin the overlap on a word boundary
    return chunks


def query_terms(question: str) -> list[str]:
    words = re.findall(r"[\w][\w\-./]*", question.lower())
    return [w for w in words if w not in STOPWORDS and (len(w) > 1 or w.isdigit())][:12]


def best_excerpt(content: str, question: str, width: int = 500) -> str:
    """The window of `content` with the most query terms (deterministic)."""
    terms = query_terms(question)
    low = content.lower()
    if len(content) <= width or not terms:
        return content[:width]
    best_at, best_score = 0, -1
    for at in range(0, max(1, len(content) - width + 1), 50):
        window = low[at:at + width]
        score = sum(window.count(t) for t in terms)
        if score > best_score:
            best_at, best_score = at, score
    start = content.rfind(" ", 0, best_at) + 1 if best_at else 0
    return content[start:start + width].strip()


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")


def key_sentences(content: str, limit: int = 5) -> list[str]:
    """Extractive summary: the sentences with the highest share of the document's frequent content words, in
    document order. Deterministic and verbatim, so every sentence is a quote."""
    sentences = [s.strip() for s in _SENTENCE.split(" ".join(content.split())) if 25 <= len(s.strip()) <= 400]
    if not sentences:
        return []
    freq: dict[str, int] = {}
    for s in sentences:
        for w in set(query_terms(s)):
            if len(w) > 2:
                freq[w] = freq.get(w, 0) + 1
    scored = []
    for i, s in enumerate(sentences):
        words = [w for w in query_terms(s) if len(w) > 2]
        score = sum(freq.get(w, 0) for w in set(words)) / (1 + len(words) ** 0.5) + (2 if i == 0 else 0)
        if re.search(r"\d", s):
            score += 1  # sentences with figures are usually the informative ones in sourcing documents
        scored.append((score, i))
    chosen = sorted(i for _, i in sorted(scored, key=lambda t: (-t[0], t[1]))[:limit])
    return [sentences[i] for i in chosen]
