import asyncio
import struct

import pytest

from app.files import scan as scanner
from tests.samples import EICAR, make_docx, make_pdf, make_png


def test_builtin_detects_eicar_anywhere():
    assert scanner.builtin_scan(b"hello " + EICAR, "text/plain").signature == "Eicar-Test-Signature"


def test_builtin_clean_files_pass():
    for data, ctype in ((make_pdf(), "application/pdf"), (make_png(), "image/png"), (b"hello", "text/plain")):
        assert scanner.builtin_scan(data, ctype).clean


def test_builtin_flags_executables_but_not_text_starting_with_mz():
    assert not scanner.builtin_scan(b"MZ\x90\x00\x03", "image/png").clean
    assert not scanner.builtin_scan(b"\x7fELF\x02", "application/pdf").clean
    assert scanner.builtin_scan(b"MZ-100 quote follows", "text/plain").clean


def test_builtin_flags_pdf_active_content():
    pdf = make_pdf().replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert(1)) >>")
    assert scanner.builtin_scan(pdf, "application/pdf").signature == "PDF.ActiveContent"


def test_builtin_flags_office_macros_and_embedded_executables():
    macro = make_docx(["x"], extra={"word/vbaProject.bin": b"\x00"})
    assert scanner.builtin_scan(macro, "application/octet-stream").signature == "Office.EmbeddedObjectOrMacro"
    exe = make_docx(["x"], extra={"word/payload.exe": b"MZ"})
    assert scanner.builtin_scan(exe, "application/octet-stream").signature == "Office.EmbeddedExecutable"


# --- ClamAV (clamd INSTREAM) against a fake server ------------------------------------
async def fake_clamd(reply: bytes):
    received = {}

    async def handle(reader, writer):
        received["command"] = await reader.readexactly(len(b"zINSTREAM\0"))
        payload = b""
        while True:
            size = struct.unpack("!I", await reader.readexactly(4))[0]
            if size == 0:
                break
            payload += await reader.readexactly(size)
        received["payload"] = payload
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], received


@pytest.fixture
def clamav_settings(monkeypatch):
    monkeypatch.setattr(scanner.settings, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(scanner.settings, "scan_backend", "both")


async def test_clamd_clean(clamav_settings, monkeypatch):
    server, port, received = await fake_clamd(b"stream: OK\0")
    monkeypatch.setattr(scanner.settings, "clamav_port", port)
    async with server:
        result = await scanner.clamd_scan(b"x" * 200_000)  # spans several protocol chunks
    assert result.clean and received["command"] == b"zINSTREAM\0" and len(received["payload"]) == 200_000


async def test_clamd_found(clamav_settings, monkeypatch):
    server, port, _ = await fake_clamd(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
    monkeypatch.setattr(scanner.settings, "clamav_port", port)
    async with server:
        result = await scanner.clamd_scan(b"data")
    assert not result.clean and result.signature == "Win.Test.EICAR_HDB-1" and result.engine == "clamav"


async def test_clamd_error_is_not_a_clean_verdict(clamav_settings, monkeypatch):
    server, port, _ = await fake_clamd(b"INSTREAM size limit exceeded. ERROR\0")
    monkeypatch.setattr(scanner.settings, "clamav_port", port)
    async with server:
        with pytest.raises(scanner.ScanUnavailable):
            await scanner.clamd_scan(b"data")


async def test_unreachable_clamd_raises_unavailable(clamav_settings, monkeypatch):
    monkeypatch.setattr(scanner.settings, "clamav_port", 1)
    with pytest.raises(scanner.ScanUnavailable):
        await scanner.scan(b"hello", "text/plain")


async def test_combined_scan_short_circuits_on_builtin_hit(clamav_settings, monkeypatch):
    monkeypatch.setattr(scanner.settings, "clamav_port", 1)  # would fail if consulted
    result = await scanner.scan(EICAR, "text/plain")
    assert not result.clean and result.engine == "builtin"


async def test_combined_scan_reports_both_engines(clamav_settings, monkeypatch):
    server, port, _ = await fake_clamd(b"stream: OK\0")
    monkeypatch.setattr(scanner.settings, "clamav_port", port)
    async with server:
        result = await scanner.scan(b"hello", "text/plain")
    assert result.clean and result.engine == "builtin+clamav"
