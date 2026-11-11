"""Virus scanning: built-in static checks plus a native ClamAV (clamd) INSTREAM client."""
import asyncio
import logging
import struct
from dataclasses import dataclass

from app.config import get_settings
from app.files.sniff import MediaTypeError, zip_entries

logger = logging.getLogger("app.scan")
settings = get_settings()

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
_EXEC_MAGIC = {
    b"MZ": "Executable.Windows",
    b"\x7fELF": "Executable.Linux",
    b"\xfe\xed\xfa\xce": "Executable.MachO",
    b"\xfe\xed\xfa\xcf": "Executable.MachO",
    b"\xcf\xfa\xed\xfe": "Executable.MachO",
    b"\xca\xfe\xba\xbe": "Executable.MachO",
}
_PDF_ACTIVE = (b"/JavaScript", b"/JS ", b"/JS(", b"/Launch", b"/RichMedia", b"/XFA")
_OFFICE_BAD_PARTS = ("vbaproject.bin", "activex", "oleobject", "embeddings/")
_OFFICE_BAD_EXT = (".exe", ".dll", ".bat", ".cmd", ".scr", ".js", ".vbs", ".ps1", ".jar")


class ScanUnavailable(Exception):
    """The scanner could not give a verdict. The job must be retried, never treated as clean."""


@dataclass
class ScanResult:
    clean: bool
    engine: str
    signature: str | None = None


def builtin_scan(data: bytes, content_type: str) -> ScanResult:
    engine = "builtin"
    if EICAR in data:
        return ScanResult(False, engine, "Eicar-Test-Signature")
    if content_type not in ("text/plain", "text/csv"):  # plain text may legitimately start with "MZ"
        for magic, name in _EXEC_MAGIC.items():
            if data.startswith(magic):
                return ScanResult(False, engine, name)
    if content_type == "application/pdf":
        for marker in _PDF_ACTIVE:
            if marker in data:
                return ScanResult(False, engine, "PDF.ActiveContent")
    try:
        names = zip_entries(data)
    except MediaTypeError:
        return ScanResult(False, engine, "Archive.Bomb")
    if names is not None:
        for name in (n.lower() for n in names):
            if any(part in name for part in _OFFICE_BAD_PARTS):
                return ScanResult(False, engine, "Office.EmbeddedObjectOrMacro")
            if name.endswith(_OFFICE_BAD_EXT):
                return ScanResult(False, engine, "Office.EmbeddedExecutable")
    return ScanResult(True, engine)


async def clamd_scan(data: bytes) -> ScanResult:
    """Stream the bytes to clamd (INSTREAM protocol)."""
    timeout = settings.clamav_timeout_seconds
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.clamav_host, settings.clamav_port), timeout=10
        )
    except (OSError, TimeoutError) as exc:
        raise ScanUnavailable(f"ClamAV unreachable: {exc}") from exc
    try:
        writer.write(b"zINSTREAM\0")
        for offset in range(0, len(data), 65536):
            chunk = data[offset : offset + 65536]
            writer.write(struct.pack("!I", len(chunk)) + chunk)
            await asyncio.wait_for(writer.drain(), timeout=timeout)
        writer.write(struct.pack("!I", 0))
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        response = (await asyncio.wait_for(reader.readuntil(b"\0"), timeout=timeout)).rstrip(b"\0")
    except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        raise ScanUnavailable(f"ClamAV scan failed: {exc}") from exc
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
    text = response.decode("utf-8", "replace").strip()
    if text.endswith("OK"):
        return ScanResult(True, "clamav")
    if text.endswith("FOUND"):
        signature = text.removeprefix("stream:").removesuffix("FOUND").strip()
        return ScanResult(False, "clamav", signature or "unknown")
    raise ScanUnavailable(f"ClamAV error: {text[:200]}")


async def scan(data: bytes, content_type: str) -> ScanResult:
    backend = settings.scan_backend
    if backend in ("both", "builtin"):
        result = builtin_scan(data, content_type)
        if not result.clean or backend == "builtin":
            return result
    result = await clamd_scan(data)
    if backend == "both" and result.clean:
        return ScanResult(True, "builtin+clamav")
    return result
