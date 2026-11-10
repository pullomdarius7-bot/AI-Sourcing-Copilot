import io
import zipfile

import pytest

from app.files import extract as ex
from app.files.sniff import DOCX, XLSX, MediaTypeError, sniff, validate_upload
from tests.samples import CSV, make_docx, make_jpeg, make_pdf, make_png, make_xlsx


# --- MIME validation ---------------------------------------------------------------
@pytest.mark.parametrize(
    "data,expected",
    [
        (make_pdf(), "application/pdf"),
        (make_docx(["x"]), DOCX),
        (make_xlsx([["a", 1]]), XLSX),
        (make_png(), "image/png"),
        (make_jpeg(), "image/jpeg"),
        (b"plain text here\n", "text/plain"),
        (CSV, "text/plain"),
    ],
)
def test_sniff_detects_by_content(data, expected):
    assert sniff(data) == expected


def test_sniff_rejects_binary_and_unknown_zip():
    assert sniff(bytes(range(256)) * 4) is None
    other_zip = io.BytesIO()
    with zipfile.ZipFile(other_zip, "w") as zf:
        zf.writestr("hello.txt", "hi")
    assert sniff(other_zip.getvalue()) is None


@pytest.mark.parametrize("name", ["a.pdf", "a.docx", "a.xlsx", "a.csv", "a.txt", "a.png", "a.jpg", "a.JPEG"])
def test_validate_accepts_matching_extension(name):
    data = {
        ".pdf": make_pdf(), ".docx": make_docx(["x"]), ".xlsx": make_xlsx([["a"]]), ".csv": CSV,
        ".txt": b"hello", ".png": make_png(), ".jpg": make_jpeg(), ".jpeg": make_jpeg(),
    }["." + name.rsplit(".", 1)[1].lower()]
    assert validate_upload(name, None, data).extension


def test_validate_rejects_extension_content_mismatch():
    with pytest.raises(MediaTypeError) as err:
        validate_upload("invoice.pdf", None, make_png())
    assert err.value.code == "mime_mismatch"


def test_validate_rejects_unsupported_extension():
    with pytest.raises(MediaTypeError) as err:
        validate_upload("run.exe", None, b"MZ\x90\x00")
    assert err.value.code == "unsupported_media_type"


def test_validate_checks_declared_type():
    with pytest.raises(MediaTypeError):
        validate_upload("a.pdf", "image/png", make_pdf())
    assert validate_upload("a.pdf", "application/octet-stream", make_pdf())
    assert validate_upload("a.csv", "application/vnd.ms-excel", CSV)
    assert validate_upload("a.pdf", "application/pdf; charset=binary", make_pdf())


def test_zip_bomb_by_entry_count_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(5_001):
            zf.writestr(f"f{i}", "")
    with pytest.raises(MediaTypeError) as err:
        sniff(buf.getvalue())
    assert err.value.code == "unsafe_archive"


def test_zip_path_traversal_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(MediaTypeError):
        sniff(buf.getvalue())


# --- Extraction ---------------------------------------------------------------------
def test_extract_pdf_text_and_pages():
    out = ex.extract("application/pdf", make_pdf("Hello quote"))
    assert "Hello quote" in out.text
    assert out.metadata["pages"] == 1 and out.metadata["has_text"] is True


def test_extract_corrupt_pdf_raises():
    with pytest.raises(ex.ExtractionError):
        ex.extract("application/pdf", b"%PDF-1.4\nthis is not a pdf")


def test_extract_docx_paragraphs_tables_and_properties():
    data = make_docx(["First paragraph", "Second"], table=[["Item", "Qty"], ["Bracket", "100"]])
    out = ex.extract(DOCX, data)
    assert "First paragraph" in out.text and "Bracket | 100" in out.text
    assert out.metadata["tables"] == 1 and out.metadata["author"] == "Ada" and out.metadata["title"] == "Quote"


def test_extract_docx_blocks_xml_entity_bombs():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        "<w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", bomb)
    with pytest.raises(ex.ExtractionError):
        ex.extract(DOCX, buf.getvalue())


def test_extract_xlsx_rows_shared_strings_and_numbers():
    out = ex.extract(XLSX, make_xlsx([["Item", "Qty", "Price"], ["Bracket", 100, 4.2]]))
    assert "## Sheet: Quotes" in out.text and "Bracket\t100\t4.2" in out.text
    assert out.metadata["sheets"] == [{"name": "Quotes", "rows": 2, "columns": 3}]


def test_extract_csv_detects_delimiter_and_header():
    out = ex.extract("text/csv", CSV)
    assert out.metadata["delimiter"] == ";" and out.metadata["rows"] == 3
    assert out.metadata["header"] == ["item", "qty", "price"]


def test_extract_txt_handles_latin1():
    out = ex.extract("text/plain", "Café Müller\nline two".encode("cp1252"))
    assert "Café Müller" in out.text and out.metadata["lines"] == 2


def test_extract_image_dimensions():
    assert ex.extract("image/png", make_png(7, 5)).metadata["width"] == 7
    jpg = ex.extract("image/jpeg", make_jpeg(9, 3)).metadata
    assert (jpg["width"], jpg["height"], jpg["needs_vision"]) == (9, 3, True)


def test_extract_truncates_long_text(monkeypatch):
    monkeypatch.setattr(ex.settings, "max_text_chars", 50)
    out = ex.extract("text/plain", b"x" * 500)
    assert len(out.text) == 50 and out.metadata["truncated"] is True
