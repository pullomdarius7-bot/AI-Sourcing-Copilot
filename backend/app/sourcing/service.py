"""Buyer sourcing requests: marketplace first, then the web, then invitations; and the supplier lead feed."""
import asyncio
import logging
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import email as mail
from app.ai import gateway
from app.ai.service import AiSubject, get_ai_service
from app.audit import record
from app.billing.plans import WEB_SOURCING
from app.config import get_settings
from app.deps import Principal
from app.errors import bad_request, conflict, not_found
from app.jobs import queue
from app.jobs.errors import PermanentError, RetryableError
from app.marketplace import profiles
from app.marketplace.catalog import CATEGORIES, CONDITIONS, REGIONS
from app.marketplace.scrub import scrub
from app.marketplace.service import require_buyer, require_supplier
from app.models import LotItem, MarketplaceLot, SourcingCandidate, SourcingRequest, User
from app.sourcing import extract, guest, invites
from app.sourcing.search import SearchError, get_search
from app.sourcing.web import Crawler, FetchError, Page, blocked, registered_domain

logger = logging.getLogger("app.sourcing")
settings = get_settings()

SEARCH = "sourcing.web_search"
STOPWORDS = {"the", "and", "for", "with", "new", "lot", "lots", "pallet", "pallets", "units", "unit", "bulk", "wholesale",
             "case", "cases", "pack", "pcs", "piece", "pieces", "box", "boxes", "mixed", "assorted", "any", "brand"}
MIN_RELEVANCE = 0.35
_http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient | None) -> None:
    """Tests route the crawler through a fake web."""
    global _http_client
    _http_client = client


def _now() -> datetime:
    return datetime.now(UTC)


def words_of(text: str) -> list[str]:
    seen: list[str] = []
    for w in re.findall(r"[a-z0-9][a-z0-9'+-]{2,}", text.lower()):
        if w not in STOPWORDS and w not in seen:
            seen.append(w)
    return seen[:8]


# --- Requests ----------------------------------------------------------------------------------------------
async def create_request(db: AsyncSession, p: Principal, data: dict[str, Any]) -> tuple[SourcingRequest, list[str]]:
    require_buyer(p)
    if data.get("category") not in CATEGORIES:
        raise bad_request("category_required", "Choose the product category")
    if data.get("condition") not in CONDITIONS:
        raise bad_request("condition_required", "Choose the condition you want")
    if data.get("region") not in REGIONS:
        raise bad_request("region_required", "Choose where the goods should be delivered")
    if not data.get("needed_by"):
        raise bad_request("date_required", "Enter the date you need the goods by")
    if data["needed_by"] < date.today():
        raise bad_request("invalid_date", "The needed-by date cannot be in the past")
    warnings: list[str] = []
    for key in ("title", "description"):
        if data.get(key):
            cleaned = scrub(data[key].strip(), names=(p.org.name,))
            data[key] = cleaned.text
            warnings += [w for w in cleaned.warnings if w not in warnings]
    request = SourcingRequest(organization_id=p.org_id, created_by=p.user.id, status="open", web_status="none", **data)
    db.add(request)
    await db.flush()
    await profiles.refresh(db, p.org_id, restore_org=p.org_id)  # suppliers see the request under this alias

    for lot, score in await marketplace_matches(db, request):
        db.add(SourcingCandidate(
            organization_id=p.org_id, request_id=request.id, kind="marketplace", lot_id=lot.id,
            company_name=lot.title, summary=f"{lot.total_units:,} units, asking ${lot.asking_price:,.2f}" if lot.asking_price else None,
            categories=lot.categories, relevance=score, method="rules", location=REGIONS.get(lot.region or "", None),
        ))
        request.marketplace_matches += 1

    wants_web = request.search_web == "always" or (request.search_web == "auto" and request.marketplace_matches == 0)
    if wants_web:
        await start_web_search(db, p, request)
    elif request.search_web == "auto":
        request.web_note = "Matching lots were found on the marketplace, so the web was not searched."
    record(db, org_id=p.org_id, user_id=p.user.id, action="sourcing.request_created", ip=p.ip,
           meta={"request_id": str(request.id), "marketplace_matches": request.marketplace_matches,
                 "web": request.web_status})
    await db.flush()
    await db.refresh(request)
    await db.commit()
    return request, warnings


async def start_web_search(db: AsyncSession, p: Principal, request: SourcingRequest, *, strict: bool = False) -> None:
    """Queue the web search. strict: the buyer asked for it explicitly, so problems are errors (409 / 402)
    instead of a note on the request."""
    if request.web_status in ("queued", "running"):
        raise conflict("already_searching", "A web search is already running for this request")
    if get_search() is None:
        if strict:
            raise conflict("web_search_unavailable", "Web search is not configured on this installation")
        request.web_status, request.web_note = "skipped", "Web search is not configured on this installation."
        return
    try:
        await gateway.check(db, p.org_id, WEB_SOURCING)
    except gateway.PaymentRequired as exc:
        if strict:
            raise
        request.web_status, request.web_note = "skipped", exc.message
        return
    request.web_status, request.web_note = "queued", None
    await queue.enqueue(db, org_id=p.org_id, kind=SEARCH, payload={"request_id": str(request.id)},
                        dedupe_key=f"{SEARCH}:{request.id}", max_attempts=2)


async def search_web_now(db: AsyncSession, p: Principal, request_id: uuid.UUID) -> SourcingRequest:
    """The buyer asks for a web search even though the marketplace had matches (or to run it again)."""
    request = await own_request(db, p, request_id)
    if request.status != "open":
        raise conflict("request_closed", "This request is closed")
    await start_web_search(db, p, request, strict=True)
    await db.commit()
    return request


async def marketplace_matches(db: AsyncSession, request: SourcingRequest) -> list[tuple[MarketplaceLot, float]]:
    words = words_of(f"{request.title} {request.description or ''}")
    if not words:
        return []
    item_match = [exists(select(LotItem.id).where(LotItem.lot_id == MarketplaceLot.id, LotItem.title.ilike(f"%{w}%")))
                  for w in words]
    stmt = select(MarketplaceLot).where(
        MarketplaceLot.status == "published", MarketplaceLot.organization_id != request.organization_id,
        or_(*[MarketplaceLot.title.ilike(f"%{w}%") for w in words], *item_match),
    ).limit(100)
    if request.category:
        stmt = stmt.where(MarketplaceLot.categories.contains([request.category]))
    lots = list((await db.scalars(stmt)).all())
    out = []
    for lot in lots:
        titles = " ".join([lot.title, lot.description or ""] + list((await db.scalars(
            select(LotItem.title).where(LotItem.lot_id == lot.id))).all())).lower()
        score = sum(1 for w in words if w in titles) / len(words)
        if score >= (0.5 if len(words) > 1 else 1.0):
            out.append((lot, round(score, 2)))
    return sorted(out, key=lambda x: -x[1])[:10]


async def own_request(db: AsyncSession, p: Principal, request_id: uuid.UUID) -> SourcingRequest:
    request = await db.get(SourcingRequest, request_id)
    if request is None or request.organization_id != p.org_id:
        raise not_found("Request not found")
    return request


# --- Web search job -----------------------------------------------------------------------------------------
def queries_for(request: SourcingRequest) -> list[str]:
    product = request.title.strip()
    queries = [f"{product} wholesale supplier", f"{product} bulk wholesale distributor"]
    if request.condition in ("customer_return", "salvage") or "liquidat" in (request.description or "").lower():
        queries.append(f"{product} liquidation pallets wholesale")
    return queries


def _request_brief(request: SourcingRequest) -> str:
    parts = [f"Product: {request.title}"]
    if request.description:
        parts.append(f"Details: {request.description}")
    if request.category:
        parts.append(f"Category: {CATEGORIES[request.category]}")
    if request.condition:
        parts.append(f"Condition: {CONDITIONS[request.condition]}")
    if request.quantity:
        parts.append(f"Quantity: {request.quantity:,} units")
    if request.target_unit_price:
        parts.append(f"Target price: ${request.target_unit_price} per unit")
    if request.region:
        parts.append(f"Deliver to: {REGIONS[request.region]} United States")
    if request.needed_by:
        parts.append(f"Needed by: {request.needed_by.isoformat()}")
    return "\n".join(parts)


async def _crawl(urls: list[str]) -> list[list[Page]]:
    semaphore = asyncio.Semaphore(4)
    async with Crawler(_http_client) as crawler:
        async def one(url: str) -> list[Page] | None:
            async with semaphore:
                try:
                    return await asyncio.wait_for(crawler.site(url), timeout=settings.web_fetch_timeout_seconds * 4)
                except (FetchError, TimeoutError):
                    return None
        results = await asyncio.gather(*(one(u) for u in urls))
    return [r for r in results if r]


async def handle_web_search(db: AsyncSession, job) -> dict[str, Any]:
    request = await db.get(SourcingRequest, uuid.UUID(job.payload["request_id"]))
    if request is None or request.status != "open":
        return {"skipped": "request closed"}
    provider = get_search()
    if provider is None:
        request.web_status, request.web_note = "skipped", "Web search is not configured on this installation."
        await db.commit()
        return {"skipped": "no provider"}
    try:
        permit = await gateway.acquire(db, request.organization_id, WEB_SOURCING, request.id, user_id=request.created_by)
    except gateway.PaymentRequired as exc:
        request.web_status, request.web_note = "skipped", exc.message
        await db.commit()
        return {"skipped": exc.code}
    request.web_status = "running"
    await db.commit()

    known = set((await db.scalars(select(SourcingCandidate.domain_hash).where(
        SourcingCandidate.request_id == request.id, SourcingCandidate.domain_hash.is_not(None)))).all())
    urls: list[str] = []
    errors: list[str] = []
    for query in queries_for(request):
        try:
            hits = await provider.search(query, 10)
        except SearchError as exc:
            errors.append(str(exc))
            continue
        for hit in hits:
            domain = registered_domain(hit.url)
            if domain and guest.domain_hash(domain) not in known and not blocked(hit.url):
                known.add(guest.domain_hash(domain))
                urls.append(hit.url)
    urls = urls[: settings.web_sourcing_max_sites]
    if not urls and errors:
        await gateway.release(db, permit)
        await db.refresh(request)
        if job.attempts < job.max_attempts:
            raise RetryableError("; ".join(errors))
        request.web_status, request.web_note = "failed", "The web search service is unavailable. No credits were used."
        await db.commit()
        return {"failed": errors}

    sites = await _crawl(urls)
    service = get_ai_service()
    words = words_of(request.title)  # the product itself; the description is context for the model
    found: list[extract.Profile] = []
    for pages in sites:
        profile = None
        if service.providers:
            try:
                result = await service.structured_call(
                    db, AiSubject(request.id, request.organization_id, registered_domain(pages[0].url)), permit=permit,
                    user_id=request.created_by, purpose="sourcing.supplier_profile", system=extract.SYSTEM,
                    schema=extract.SCHEMA, schema_name="supplier_profile",
                    text=f"BUYER REQUEST\n{_request_brief(request)}\n\nWEBSITE PAGES\n{extract.pages_prompt(pages)}",
                    validate=extract.validate, instruction="Return the supplier profile for the website above.",
                )
                profile = extract.from_model(result.value, pages)
            except (PermanentError, RetryableError) as exc:
                logger.warning("supplier extraction fell back to rules for %s: %s", pages[0].url, exc)
        profile = profile or extract.from_rules(pages, words)
        if profile.is_supplier and profile.relevance >= MIN_RELEVANCE:
            found.append(profile)

    await db.refresh(request)
    found.sort(key=lambda pr: -pr.relevance)
    candidates: list[tuple[extract.Profile, SourcingCandidate]] = []
    for profile in found:
        # The buyer's record never holds the supplier's identity (name, site, email, phone): it stays in the
        # platform-only invitation, so the deal is made on the platform.
        candidate = SourcingCandidate(
            organization_id=request.organization_id, request_id=request.id, kind="web",
            business_type=profile.business_type, categories=profile.categories, relevance=profile.relevance,
            method=profile.method, flags=profile.flags, **guest.masked_profile(profile),
        )
        db.add(candidate)
        candidates.append((profile, candidate))
    await db.flush()
    request.web_matches = (request.web_matches or 0) + len(candidates)
    request.web_status, request.completed_at = "done", _now()
    if candidates:
        await gateway.settle(db, permit)
        request.web_note = f"Searched {len(urls)} website(s); {len(candidates)} matched."
        for profile, candidate in candidates[: settings.invites_per_request]:  # the most relevant only
            candidate.invite_status = await invites.consider(db, request, profile, candidate)
        request.invites_sent += sum(1 for _, c in candidates if c.invite_status == "queued")
    else:
        request.web_note = (f"Searched {len(urls)} website(s) and found no supplier that matches. "
                            "The credits were refunded.")
    record(db, org_id=request.organization_id, user_id=request.created_by, action="sourcing.web_search_done",
           meta={"request_id": str(request.id), "sites": len(urls), "matches": len(candidates)})
    creator = await db.get(User, request.created_by) if request.created_by else None
    await db.commit()
    if not candidates:
        await gateway.release(db, permit)
    if creator is not None:
        await mail.send_email(creator.email, f"Sourcing results: {request.title}", (
            f"The AI sourcing engine finished searching the web for \"{request.title}\".\n\n"
            f"{request.web_note}\n\nSee the suppliers: {settings.app_base_url}/sourcing/{request.id}\n"))
    return {"sites": len(urls), "matches": len(candidates)}


async def dead_web_search(db: AsyncSession, job, error: str) -> None:
    try:
        request = await db.get(SourcingRequest, uuid.UUID(job.payload["request_id"]))
    except (KeyError, ValueError):
        return
    if request is not None and request.web_status in ("queued", "running"):
        request.web_status, request.web_note = "failed", "The web search could not be completed. No credits were used."
        await db.commit()


# --- Candidate actions --------------------------------------------------------------------------------------
async def own_candidate(db: AsyncSession, p: Principal, candidate_id: uuid.UUID) -> SourcingCandidate:
    candidate = await db.get(SourcingCandidate, candidate_id)
    if candidate is None or candidate.organization_id != p.org_id:
        raise not_found("Supplier not found")
    return candidate


# --- Supplier lead feed ---------------------------------------------------------------------------------------
async def leads(db: AsyncSession, p: Principal) -> list[tuple[SourcingRequest, float]]:
    """Open buyer requests, best match for this supplier's inventory first."""
    require_supplier(p)
    requests = list((await db.scalars(select(SourcingRequest).where(
        SourcingRequest.status == "open", SourcingRequest.open_to_suppliers.is_(True),
        SourcingRequest.organization_id != p.org_id,
    ).order_by(SourcingRequest.created_at.desc()).limit(200))).all())
    mine = (await db.execute(select(MarketplaceLot.categories, MarketplaceLot.title).where(
        MarketplaceLot.organization_id == p.org_id, MarketplaceLot.status.in_(("published", "draft"))))).all()
    categories = {c for cats, _ in mine for c in cats}
    titles = " ".join(t for _, t in mine).lower()
    scored = []
    for r in requests:
        words = words_of(r.title)
        score = (0.6 if r.category and r.category in categories else 0.0) + \
            (0.4 * sum(1 for w in words if w in titles) / len(words) if words else 0.0)
        scored.append((r, round(score, 2)))
    return sorted(scored, key=lambda x: (-x[1], -x[0].created_at.timestamp()))


async def lead(db: AsyncSession, p: Principal, request_id: uuid.UUID) -> SourcingRequest:
    require_supplier(p)
    request = await db.get(SourcingRequest, request_id)
    if request is None or request.status != "open" or not request.open_to_suppliers:
        raise not_found("This request is no longer open")
    return request


def money(value: Decimal | None) -> str | None:
    return f"${value:,.2f}" if value is not None else None
