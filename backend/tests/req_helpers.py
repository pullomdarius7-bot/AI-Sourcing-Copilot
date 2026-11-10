from app.ai.service import AIService, set_ai_service
from tests import extraction_fixtures as fx
from tests.fakes import FakeProvider
from tests.helpers import add_member, register_admin


async def ready_document(client, worker, who, text=fx.RFQ_TEXT, name="rfq.txt"):
    """Upload and fully process a document (no AI provider is installed yet, so analysis is skipped)."""
    res = await client.post("/documents", files={"file": (name, text.encode())}, headers=who.headers)
    assert res.status_code == 201, res.text
    await worker.drain()
    return res.json()["document"]["id"]


async def extract(client, worker, who, doc_id, script=None, **provider_kwargs):
    provider = FakeProvider(script or [fx.good_extraction()], **provider_kwargs)
    set_ai_service(AIService([provider]))
    res = await client.post(f"/documents/{doc_id}/requirements/extract", headers=who.headers)
    assert res.status_code == 202, res.text
    await worker.drain()
    return res.json()["id"], provider


async def detail(client, who, run_id):
    res = await client.get(f"/extraction-runs/{run_id}", headers=who.headers)
    assert res.status_code == 200, res.text
    return res.json()


def all_params(d):
    return [p for r in d["requirements"] for p in r["parameters"]] + d["unknown_parameters"]


def param(d, name):
    return next(p for p in all_params(d) if p["name"] == name)


def requirement(d, startswith):
    return next(r for r in d["requirements"] if r["statement"].startswith(startswith) or r["source_quote"].startswith(startswith))


class Team:
    pass


async def team(client, worker, script=None):
    """Org with a requester (starts extraction), buyer (reviews), approver (approves), admin and auditor."""
    t = Team()
    t.admin = await register_admin(client)
    t.requester = await add_member(client, t.admin, "req@acme.test", "requester")
    t.buyer = await add_member(client, t.admin, "buyer@acme.test", "buyer")
    t.approver = await add_member(client, t.admin, "approver@acme.test", "approver")
    t.auditor = await add_member(client, t.admin, "auditor@acme.test", "auditor")
    t.doc_id = await ready_document(client, worker, t.requester)
    t.run_id, t.provider = await extract(client, worker, t.requester, t.doc_id, script)
    t.detail = await detail(client, t.buyer, t.run_id)
    return t


async def resolve_all(client, who, run_id, d=None):
    """Verify every unresolved item (what a diligent reviewer ends up doing)."""
    d = d or await detail(client, who, run_id)
    for r in d["requirements"]:
        if r["validation_state"] == "unverified":
            assert (await client.post(f"/requirements/{r['id']}/verify", json={}, headers=who.headers)).status_code == 200
    for p in all_params(d):
        if p["validation_state"] == "unverified":
            assert (await client.post(f"/parameters/{p['id']}/verify", json={}, headers=who.headers)).status_code == 200
    return await detail(client, who, run_id)
