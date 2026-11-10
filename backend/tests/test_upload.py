import pytest

from tests.helpers import add_member, register_admin
from tests.samples import CSV, EICAR, QUOTE, make_docx, make_jpeg, make_pdf, make_png, make_xlsx


async def upload(client, acct, name, data, content_type=None):
    file = (name, data, content_type) if content_type else (name, data)
    return await client.post("/documents", files={"file": file}, headers=acct.headers)


async def get_doc(client, acct, doc_id):
    res = await client.get(f"/documents/{doc_id}", headers=acct.headers)
    assert res.status_code == 200, res.text
    return res.json()


# --- Upload validation --------------------------------------------------------------
async def test_upload_creates_document_and_queues_scan(client):
    admin = await register_admin(client)
    res = await upload(client, admin, "quote.txt", QUOTE.encode())
    assert res.status_code == 201
    body = res.json()
    doc = body["document"]
    assert body["duplicate"] is False
    assert doc["status"] == "uploaded" and doc["scan_status"] == "pending"
    assert doc["content_type"] == "text/plain" and doc["size_bytes"] == len(QUOTE)
    assert len(doc["sha256"]) == 64 and doc["storage_backend"] == "local"
    detail = await get_doc(client, admin, doc["id"])
    assert [(j["kind"], j["status"]) for j in detail["jobs"]] == [("document.scan", "queued")]


@pytest.mark.parametrize(
    "name,data,expected_status,code",
    [
        ("run.exe", b"MZ\x90\x00", 415, "unsupported_media_type"),
        ("notes.pdf", make_png(), 422, "mime_mismatch"),
        ("image.png", b"just text", 422, "mime_mismatch"),
        ("empty.txt", b"", 400, "empty_file"),
        ("huge.txt", b"a" * (1024 * 1024 + 1), 413, "file_too_large"),
    ],
)
async def test_upload_rejects_invalid_files(client, name, data, expected_status, code):
    admin = await register_admin(client)
    res = await upload(client, admin, name, data)
    assert res.status_code == expected_status and res.json()["error"]["code"] == code


async def test_upload_rejects_wrong_declared_mime(client):
    admin = await register_admin(client)
    res = await upload(client, admin, "a.pdf", make_pdf(), "image/png")
    assert res.status_code == 422


async def test_upload_sanitizes_filename(client):
    admin = await register_admin(client)
    res = await upload(client, admin, "../../etc/evil.txt", b"hello")
    assert res.status_code == 201 and res.json()["document"]["filename"] == "evil.txt"


async def test_upload_requires_file_and_auth(client):
    admin = await register_admin(client)
    assert (await client.post("/documents", headers=admin.headers)).status_code == 422
    assert (await client.post("/documents", files={"file": ("a.txt", b"x")})).status_code == 401


# --- Dedup --------------------------------------------------------------------------
async def test_duplicate_upload_returns_existing_document(client):
    admin = await register_admin(client)
    first = (await upload(client, admin, "a.txt", b"identical content")).json()["document"]
    again = await upload(client, admin, "renamed.txt", b"identical content")
    assert again.status_code == 200
    assert again.json()["duplicate"] is True and again.json()["document"]["id"] == first["id"]
    listing = (await client.get("/documents", headers=admin.headers)).json()
    assert listing["total"] == 1
    actions = [r["action"] for r in (await client.get("/audit-logs", headers=admin.headers)).json()]
    assert "document.duplicate_detected" in actions


async def test_dedup_is_per_organization(client):
    a = await register_admin(client, email="a@alpha.test", org="Alpha")
    b = await register_admin(client, email="b@beta.test", org="Beta")
    ra = await upload(client, a, "same.txt", b"shared bytes")
    rb = await upload(client, b, "same.txt", b"shared bytes")
    assert ra.status_code == rb.status_code == 201
    assert ra.json()["document"]["id"] != rb.json()["document"]["id"]
    assert ra.json()["document"]["sha256"] == rb.json()["document"]["sha256"]


# --- Pipeline -----------------------------------------------------------------------
FILES = {
    "quote.pdf": make_pdf("Quote total 420 EUR"),
    "quote.docx": make_docx(["Supplier: Acme"], table=[["Item", "Qty"], ["Bolt", "5"]]),
    "quote.xlsx": make_xlsx([["Item", "Qty"], ["Bolt", 5]]),
    "quote.csv": CSV,
    "quote.txt": QUOTE.encode(),
    "photo.png": make_png(),
    "photo.jpg": make_jpeg(),
}


@pytest.mark.parametrize("name", list(FILES))
async def test_pipeline_processes_every_supported_type(client, worker, name):
    admin = await register_admin(client)
    doc_id = (await upload(client, admin, name, FILES[name])).json()["document"]["id"]
    assert await worker.drain() == 3  # scan -> extract -> analyze
    doc = await get_doc(client, admin, doc_id)
    assert doc["status"] == "processed" and doc["scan_status"] == "clean"
    assert doc["scan_engine"] == "builtin"
    assert doc["analysis_status"] == "skipped"  # no AI provider configured
    assert "No AI provider" in doc["file_metadata"]["analysis_skipped_reason"]
    assert doc["file_metadata"]["text_chars"] >= 0
    assert [j["status"] for j in doc["jobs"]] == ["succeeded"] * 3
    if name.endswith(".pdf"):
        assert doc["file_metadata"]["pages"] == 1
    if name.endswith((".png", ".jpg")):
        assert doc["file_metadata"]["width"] > 0 and doc["text_preview"] is None
    if name.endswith(".txt"):
        assert "Northwind Metals" in doc["text_preview"]


async def test_download_returns_original_bytes_after_clean_scan(client, worker):
    admin = await register_admin(client)
    doc_id = (await upload(client, admin, "quote é.txt", b"original bytes")).json()["document"]["id"]
    assert (await client.get(f"/documents/{doc_id}/download", headers=admin.headers)).status_code == 409
    await worker.drain()
    res = await client.get(f"/documents/{doc_id}/download", headers=admin.headers)
    assert res.status_code == 200 and res.content == b"original bytes"
    assert res.headers["content-disposition"].startswith("attachment; filename*=UTF-8''quote%20%C3%A9")
    assert res.headers["x-content-type-options"] == "nosniff"


async def test_infected_file_is_quarantined_and_blocked(client, worker):
    admin = await register_admin(client)
    doc_id = (await upload(client, admin, "bad.txt", b"hello " + EICAR)).json()["document"]["id"]
    assert await worker.drain() == 1  # nothing after the scan
    doc = await get_doc(client, admin, doc_id)
    assert doc["status"] == "quarantined" and doc["scan_status"] == "infected"
    assert doc["scan_detail"] == "Eicar-Test-Signature" and doc["text_preview"] is None
    assert (await client.get(f"/documents/{doc_id}/download", headers=admin.headers)).status_code == 409
    assert (await client.post(f"/documents/{doc_id}/reprocess", headers=admin.headers)).status_code == 409
    again = await upload(client, admin, "bad.txt", b"hello " + EICAR)
    assert again.json()["duplicate"] is True and again.json()["document"]["status"] == "quarantined"
    actions = [r["action"] for r in (await client.get("/audit-logs", headers=admin.headers)).json()]
    assert "document.quarantined" in actions


async def test_corrupt_file_goes_to_dead_letter_and_fails_document(client, worker):
    admin = await register_admin(client)
    doc_id = (await upload(client, admin, "broken.pdf", b"%PDF-1.4\nnot really a pdf")).json()["document"]["id"]
    await worker.drain()
    doc = await get_doc(client, admin, doc_id)
    assert doc["status"] == "failed" and "Unreadable PDF" in doc["error"]
    extract_job = next(j for j in doc["jobs"] if j["kind"] == "document.extract")
    assert extract_job["status"] == "dead" and extract_job["attempts"] == 1  # permanent: no retries
    reprocess = await client.post(f"/documents/{doc_id}/reprocess", headers=admin.headers)
    assert reprocess.status_code == 200 and reprocess.json()["status"] == "uploaded"
    await worker.drain()
    assert (await get_doc(client, admin, doc_id))["status"] == "failed"


async def test_reprocess_processed_document(client, worker):
    admin = await register_admin(client)
    doc_id = (await upload(client, admin, "a.txt", b"hello")).json()["document"]["id"]
    await worker.drain()
    res = await client.post(f"/documents/{doc_id}/reprocess", headers=admin.headers)
    assert res.status_code == 200
    assert (await client.post(f"/documents/{doc_id}/reprocess", headers=admin.headers)).status_code == 409
    await worker.drain()
    assert (await get_doc(client, admin, doc_id))["status"] == "processed"


# --- Listing, deletion --------------------------------------------------------------
async def test_list_filters_search_and_paging(client, worker):
    admin = await register_admin(client)
    for i in range(3):
        await upload(client, admin, f"quote-{i}.txt", f"content {i}".encode())
    await upload(client, admin, "bad.txt", EICAR)
    await worker.drain()
    everything = (await client.get("/documents?limit=2", headers=admin.headers)).json()
    assert everything["total"] == 4 and len(everything["items"]) == 2
    page2 = (await client.get("/documents?limit=2&offset=2", headers=admin.headers)).json()
    assert {d["id"] for d in everything["items"]}.isdisjoint({d["id"] for d in page2["items"]})
    quarantined = (await client.get("/documents?status=quarantined", headers=admin.headers)).json()
    assert quarantined["total"] == 1
    found = (await client.get("/documents?q=quote-1", headers=admin.headers)).json()
    assert [d["filename"] for d in found["items"]] == ["quote-1.txt"]
    assert (await client.get("/documents?q=%25", headers=admin.headers)).json()["total"] == 0  # wildcard escaped
    assert (await client.get("/documents?status=bogus", headers=admin.headers)).status_code == 422


async def test_delete_removes_document_and_stored_file(client, worker):
    from app.storage import get_storage

    admin = await register_admin(client)
    doc = (await upload(client, admin, "a.txt", b"to be deleted")).json()["document"]
    await worker.drain()
    assert (await client.delete(f"/documents/{doc['id']}", headers=admin.headers)).status_code == 200
    assert (await client.get(f"/documents/{doc['id']}", headers=admin.headers)).status_code == 404
    key = f"{admin.org_id}/{doc['sha256']}"
    with pytest.raises(Exception):
        await get_storage().get("local", key)
    assert (await upload(client, admin, "a.txt", b"to be deleted")).status_code == 201  # no longer a duplicate


# --- RBAC & tenant isolation -----------------------------------------------------------
@pytest.mark.parametrize(
    "role,can_upload,can_delete",
    [("requester", True, False), ("buyer", True, True), ("approver", False, False), ("auditor", False, False)],
)
async def test_document_permissions_by_role(client, role, can_upload, can_delete):
    admin = await register_admin(client)
    seeded = (await upload(client, admin, "seed.txt", b"seed")).json()["document"]
    member = await add_member(client, admin, f"{role}@acme.test", role)
    assert (await client.get("/documents", headers=member.headers)).status_code == 200
    assert (await client.get(f"/documents/{seeded['id']}", headers=member.headers)).status_code == 200
    res = await upload(client, member, "mine.txt", f"from {role}".encode())
    assert res.status_code == (201 if can_upload else 403)
    res = await client.delete(f"/documents/{seeded['id']}", headers=member.headers)
    assert res.status_code == (200 if can_delete else 403)


async def test_documents_are_isolated_between_organizations(client, worker):
    a = await register_admin(client, email="a@alpha.test", org="Alpha")
    b = await register_admin(client, email="b@beta.test", org="Beta")
    doc_id = (await upload(client, a, "secret.txt", b"alpha only")).json()["document"]["id"]
    await worker.drain()
    for method, path in [
        ("GET", f"/documents/{doc_id}"), ("GET", f"/documents/{doc_id}/download"),
        ("GET", f"/documents/{doc_id}/analysis"), ("DELETE", f"/documents/{doc_id}"),
        ("POST", f"/documents/{doc_id}/reprocess"),
    ]:
        res = await client.request(method, path, headers=b.headers)
        assert res.status_code == 404, (method, path)
    assert (await client.get("/documents", headers=b.headers)).json() == {"items": [], "total": 0}
    assert (await client.get("/jobs", headers=b.headers)).json() == []
    assert (await get_doc(client, a, doc_id))["status"] == "processed"


async def test_limits_endpoint(client):
    admin = await register_admin(client)
    body = (await client.get("/documents/limits", headers=admin.headers)).json()
    assert body["max_upload_mb"] == 1 and ".pdf" in body["extensions"] and ".jpg" in body["extensions"]
