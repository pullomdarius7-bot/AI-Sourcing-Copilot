"""Content-based type detection. The file's bytes decide the type, never its name or declared MIME."""
import io
import zipfile
from dataclasses import dataclass

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# extension -> canonical stored MIME type
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": DOCX,
    ".xlsx": XLSX,
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# what the byte-sniffer must report for each extension
_SNIFF_FOR_EXT = {
    ".pdf": "application/pdf",
    ".docx": DOCX,
    ".xlsx": XLSX,
    ".csv": "text/plain",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# MIME types a client may legitimately declare for each extension
_DECLARED_OK: dict[str, set[str]] = {
    ".pdf": {"application/pdf", "application/x-pdf"},
    ".docx": {DOCX},
    ".xlsx": {XLSX},
    ".csv": {"text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"},
    ".txt": {"text/plain"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg", "image/jpg", "image/pjpeg"},
    ".jpeg": {"image/jpeg", "image/jpg", "image/pjpeg"},
}
_GENERIC = {"", "application/octet-stream", "binary/octet-stream"}

MAX_ARCHIVE_ENTRIES = 5_000
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


class MediaTypeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Detected:
    extension: str
    content_type: str  # canonical type to store
    sniffed: str


def extension_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def zip_entries(data: bytes) -> list[str] | None:
    """Entry names of a safe ZIP archive; None if not a ZIP. Raises MediaTypeError on archive bombs."""
    if not data.startswith(b"PK\x03\x04"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise MediaTypeError("unsafe_archive", "The document contains too many parts")
            if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
                raise MediaTypeError("unsafe_archive", "The document expands to an unsafe size")
            if any(i.filename.startswith(("/", "..")) or ".." in i.filename.split("/") for i in infos):
                raise MediaTypeError("unsafe_archive", "The document contains unsafe paths")
            return [i.filename for i in infos]
    except zipfile.BadZipFile:
        return None


def _looks_like_text(data: bytes) -> bool:
    sample = data[:16384]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            sample.decode("utf-16")
            return True
        except UnicodeDecodeError:
            return False
    if b"\x00" in sample:
        return False
    control = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13))
    if sample and control / len(sample) > 0.01:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        # a multi-byte character cut at the sample boundary is fine
        return exc.start >= len(sample) - 3 or _latin1_plausible(sample)


def _latin1_plausible(sample: bytes) -> bool:
    high = sum(1 for b in sample if b >= 0x80)
    return high / max(len(sample), 1) < 0.3


def sniff(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    names = zip_entries(data)
    if names is not None:
        if "word/document.xml" in names and "[Content_Types].xml" in names:
            return DOCX
        if "xl/workbook.xml" in names and "[Content_Types].xml" in names:
            return XLSX
        return None
    if data and _looks_like_text(data):
        return "text/plain"
    return None


def validate_upload(filename: str, declared: str | None, data: bytes) -> Detected:
    ext = extension_of(filename)
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise MediaTypeError("unsupported_media_type", f"Unsupported file type. Allowed: {allowed}")
    sniffed = sniff(data)
    if sniffed is None or sniffed != _SNIFF_FOR_EXT[ext]:
        raise MediaTypeError(
            "mime_mismatch", f"The file content does not match its {ext} extension"
        )
    declared_base = (declared or "").split(";")[0].strip().lower()
    if declared_base not in _GENERIC and declared_base not in _DECLARED_OK[ext]:
        raise MediaTypeError(
            "mime_mismatch", f"Declared type {declared_base} does not match a {ext} file"
        )
    return Detected(extension=ext, content_type=ALLOWED_EXTENSIONS[ext], sniffed=sniffed)
