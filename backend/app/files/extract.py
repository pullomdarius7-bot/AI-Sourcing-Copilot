"""Metadata and text extraction. OOXML/CSV/TXT/image parsing is native; PDF uses pypdf."""
import csv
import io
import re
import struct
import zipfile
from dataclasses import dataclass, field
from typing import Any

from defusedxml import ElementTree as ET  # hardened against entity-expansion attacks
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.config import get_settings
from app.files.sniff import DOCX, XLSX

settings = get_settings()
MAX_ROWS = 5_000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties/}"
_DCTERMS = "{http://purl.org/dc/terms/}"


class ExtractionError(Exception):
    """The file is corrupt or unreadable. Retrying will not help."""


@dataclass
class Extracted:
    metadata: dict[str, Any] = field(default_factory=dict)
    text: str = ""


def clean_text(text: str) -> tuple[str, bool]:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    limit = settings.max_text_chars
    if len(text) > limit:
        return text[:limit], True
    return text, False


def decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding), encoding.replace("-sig", "")
        except UnicodeDecodeError:
            continue
    raise ExtractionError("Cannot decode text")  # pragma: no cover


def _xml(zf: zipfile.ZipFile, name: str):
    try:
        return ET.fromstring(zf.read(name))
    except KeyError:
        return None
    except (ET.ParseError, DefusedXmlException) as exc:
        raise ExtractionError(f"Invalid XML in {name}") from exc


def _core_properties(zf: zipfile.ZipFile) -> dict[str, Any]:
    root = _xml(zf, "docProps/core.xml")
    if root is None:
        return {}
    pairs = {
        "title": f"{_DC}title", "author": f"{_DC}creator", "subject": f"{_DC}subject",
        "last_modified_by": f"{_CP}lastModifiedBy", "created": f"{_DCTERMS}created",
        "modified": f"{_DCTERMS}modified",
    }
    out = {}
    for key, tag in pairs.items():
        node = root.find(tag)
        if node is not None and node.text:
            out[key] = node.text.strip()[:300]
    return out


# --- PDF ---------------------------------------------------------------------
def extract_pdf(data: bytes) -> Extracted:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError("The PDF is password protected")
        info = reader.metadata or {}
        meta: dict[str, Any] = {"pages": len(reader.pages)}
        for key, attr in (("title", "title"), ("author", "author"), ("producer", "producer"),
                          ("creator", "creator"), ("created", "creation_date_raw")):
            value = getattr(info, attr, None)
            if value:
                meta[key] = str(value)[:300]
        parts: list[str] = []
        total = 0
        for page in reader.pages:
            page_text = page.extract_text() or ""
            parts.append(page_text)
            total += len(page_text)
            if total > settings.max_text_chars * 2:
                break
    except ExtractionError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, RecursionError, OSError) as exc:
        raise ExtractionError(f"Unreadable PDF: {exc}") from exc
    text, truncated = clean_text("\n\n".join(parts))
    meta["has_text"] = bool(text)
    meta["truncated"] = truncated
    if not text:
        meta["needs_ocr"] = True
    return Extracted(meta, text)


# --- DOCX --------------------------------------------------------------------
def extract_docx(data: bytes) -> Extracted:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            body = _xml(zf, "word/document.xml")
            if body is None:
                raise ExtractionError("Missing word/document.xml")
            meta = _core_properties(zf)
    except zipfile.BadZipFile as exc:
        raise ExtractionError("Corrupt DOCX") from exc

    def para_text(p) -> str:
        return "".join(t.text or "" for t in p.iter(f"{_W}t"))

    lines: list[str] = []
    tables = 0
    container = body.find(f"{_W}body")
    for child in list(container) if container is not None else []:
        if child.tag == f"{_W}p":
            lines.append(para_text(child))
        elif child.tag == f"{_W}tbl":
            tables += 1
            for tr in child.iter(f"{_W}tr"):
                cells = [" ".join(para_text(p) for p in tc.iter(f"{_W}p")).strip() for tc in tr.findall(f"{_W}tc")]
                lines.append(" | ".join(cells))
    text, truncated = clean_text("\n".join(lines))
    meta.update(paragraphs=sum(1 for ln in lines if ln), tables=tables, truncated=truncated)
    return Extracted(meta, text)


# --- XLSX --------------------------------------------------------------------
def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref.upper())
    n = 0
    for ch in letters.group(0) if letters else "A":
        n = n * 26 + ord(ch) - 64
    return n - 1


def extract_xlsx(data: bytes) -> Extracted:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            workbook = _xml(zf, "xl/workbook.xml")
            if workbook is None:
                raise ExtractionError("Missing workbook")
            rels = _xml(zf, "xl/_rels/workbook.xml.rels")
            targets = {}
            if rels is not None:
                for rel in rels.findall(f"{_PKG_REL}Relationship"):
                    target = rel.get("Target", "")
                    targets[rel.get("Id")] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            shared: list[str] = []
            sst = _xml(zf, "xl/sharedStrings.xml")
            if sst is not None:
                for si in sst.findall(f"{_S}si"):
                    shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
            meta = _core_properties(zf)
            sheets_meta = []
            chunks: list[str] = []
            sheets = workbook.find(f"{_S}sheets")
            for sheet in list(sheets) if sheets is not None else []:
                name = sheet.get("name", "Sheet")
                part = targets.get(sheet.get(f"{_R}id"))
                root = _xml(zf, part) if part else None
                rows: list[list[str]] = []
                if root is not None:
                    for row in root.iter(f"{_S}row"):
                        if len(rows) >= MAX_ROWS:
                            break
                        values: dict[int, str] = {}
                        for c in row.findall(f"{_S}c"):
                            kind = c.get("t")
                            v = c.find(f"{_S}v")
                            if kind == "inlineStr":
                                val = "".join(t.text or "" for t in c.iter(f"{_S}t"))
                            elif v is None or v.text is None:
                                continue
                            elif kind == "s":
                                idx = int(v.text)
                                val = shared[idx] if idx < len(shared) else ""
                            else:
                                val = v.text
                            values[_col_index(c.get("r", "A1"))] = val
                        if values:
                            rows.append([values.get(i, "") for i in range(max(values) + 1)])
                sheets_meta.append(
                    {"name": name, "rows": len(rows), "columns": max((len(r) for r in rows), default=0)}
                )
                chunks.append(f"## Sheet: {name}\n" + "\n".join("\t".join(r) for r in rows))
    except zipfile.BadZipFile as exc:
        raise ExtractionError("Corrupt XLSX") from exc
    except (ValueError, IndexError) as exc:
        raise ExtractionError(f"Invalid worksheet data: {exc}") from exc
    text, truncated = clean_text("\n\n".join(chunks))
    meta.update(sheets=sheets_meta, truncated=truncated)
    return Extracted(meta, text)


# --- CSV / TXT ---------------------------------------------------------------
def extract_csv(data: bytes) -> Extracted:
    raw, encoding = decode_text(data)
    try:
        delimiter = csv.Sniffer().sniff(raw[:8192], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
    rows: list[list[str]] = []
    try:
        for row in reader:
            rows.append(row)
            if len(rows) >= MAX_ROWS + 1:
                break
    except csv.Error as exc:
        raise ExtractionError(f"Invalid CSV: {exc}") from exc
    text, truncated = clean_text(raw)
    meta = {
        "encoding": encoding, "delimiter": delimiter, "rows": len(rows),
        "columns": max((len(r) for r in rows), default=0),
        "header": rows[0][:50] if rows else [], "truncated": truncated,
    }
    return Extracted(meta, text)


def extract_txt(data: bytes) -> Extracted:
    raw, encoding = decode_text(data)
    text, truncated = clean_text(raw)
    return Extracted({"encoding": encoding, "lines": raw.count("\n") + 1, "chars": len(raw), "truncated": truncated}, text)


# --- Images ------------------------------------------------------------------
def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise ExtractionError("Corrupt PNG")
    return struct.unpack(">II", data[16:24])


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    raise ExtractionError("Corrupt JPEG")


def extract_image(data: bytes, content_type: str) -> Extracted:
    width, height = _png_dimensions(data) if content_type == "image/png" else _jpeg_dimensions(data)
    if not (0 < width <= 20_000 and 0 < height <= 20_000):
        raise ExtractionError("Implausible image dimensions")
    return Extracted({"width": width, "height": height, "needs_vision": True}, "")


def extract(content_type: str, data: bytes) -> Extracted:
    if content_type == "application/pdf":
        return extract_pdf(data)
    if content_type == DOCX:
        return extract_docx(data)
    if content_type == XLSX:
        return extract_xlsx(data)
    if content_type == "text/csv":
        return extract_csv(data)
    if content_type == "text/plain":
        return extract_txt(data)
    if content_type in ("image/png", "image/jpeg"):
        return extract_image(data, content_type)
    raise ExtractionError(f"Unsupported content type {content_type}")
