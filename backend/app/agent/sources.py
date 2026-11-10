"""Sources: the only things an answer may state facts from."""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

MAX_CONTENT = 6000
MAX_EXCERPT = 600


@dataclass
class Source:
    key: str  # stable identity, e.g. "rfq:<uuid>" or "chunk:<uuid>"
    kind: str  # rfq | supplier | document | comparison | analysis | quote | glossary | method | report | requirements | queue
    title: str
    path: str | None  # app route the user can open, or None
    content: str  # everything this source contributes; answers are checked against it
    ref: str | None = None  # entity id
    as_of: str | None = None
    id: str = ""  # S1, S2, ... assigned by the book

    def public(self) -> dict[str, Any]:
        excerpt = self.content if len(self.content) <= MAX_EXCERPT else self.content[: MAX_EXCERPT - 1] + "…"
        return {"id": self.id, "kind": self.kind, "title": self.title, "path": self.path, "ref": self.ref,
                "as_of": self.as_of, "excerpt": excerpt,
                "sha256": hashlib.sha256(self.content.encode()).hexdigest()}


@dataclass
class SourceBook:
    sources: list[Source] = field(default_factory=list)
    _by_key: dict[str, Source] = field(default_factory=dict)

    def add(self, source: Source) -> str:
        existing = self._by_key.get(source.key)
        if existing is not None:
            if source.content not in existing.content:
                existing.content = (existing.content + "\n" + source.content)[:MAX_CONTENT]
            return existing.id
        source.content = source.content[:MAX_CONTENT]
        source.id = f"S{len(self.sources) + 1}"
        self.sources.append(source)
        self._by_key[source.key] = source
        return source.id

    def get(self, source_id: str) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)

    def ids(self) -> set[str]:
        return {s.id for s in self.sources}

    def used(self, ids: set[str]) -> list[dict[str, Any]]:
        return [s.public() for s in self.sources if s.id in ids]

    def for_prompt(self) -> str:
        """Sources as data for the model. Content is fenced and labelled as untrusted."""
        parts = []
        for s in self.sources:
            parts.append(json.dumps({"id": s.id, "kind": s.kind, "title": s.title, "content": s.content}, ensure_ascii=False))
        return "\n".join(parts)


@dataclass
class Fact:
    """One statement the agent can make, with the sources that support it."""

    text: str
    sources: list[str]
    weight: int = 0  # ordering hint for the writer; higher first
