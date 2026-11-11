"""A small fake web for the sourcing engine: a search provider and websites served through httpx.MockTransport."""
import httpx

from app.sourcing.search import Hit, SearchProvider

ACME_HOME = """<html><head><title>Acme Tool Wholesale | Bulk power tools for resellers</title>
<meta property="og:site_name" content="Acme Tool Wholesale">
<meta name="description" content="Wholesale distributor of power tools for resellers."></head>
<body><nav><a href="/">Home</a> <a href="/contact">Contact us</a> <a href="/wholesale-program">Wholesale program</a></nav>
<h1>Acme Tool Wholesale</h1>
<p>We supply resellers and liquidators with bulk cordless drills and impact drivers.</p>
<p>DeWalt 20V MAX cordless drill kits in case packs of 10.</p>
<p>Minimum order: 50 units. Wholesale pricing from $65 per unit.</p>
<script>var ignore = "do not read me";</script>
</body></html>"""

ACME_CONTACT = """<html><head><title>Contact - Acme Tool Wholesale</title></head><body>
<h1>Contact our wholesale team</h1>
<p>Email <a href="mailto:sales@acmetools.example">sales@acmetools.example</a> or call (312) 555-0100.</p>
<address>2200 S Ashland Ave, Chicago, IL</address></body></html>"""

ACME_WHOLESALE = """<html><head><title>Wholesale program</title></head><body>
<p>Ships nationwide from our Chicago warehouse on pallets. Net 30 terms for approved resellers.</p></body></html>"""

BLOG = """<html><head><title>Drill reviews 2026</title></head><body>
<p>Our favourite cordless drill for weekend projects. We earn a commission on links.</p></body></html>"""

BEAN = """<html><head><title>Bean Bulk Liquidation</title></head><body>
<p>Bean Bulk Liquidation sells pallets of customer return power tools and cordless drill lots to resellers.</p>
<p>Contact: beanbulk@gmail.com</p></body></html>"""

SITES = {
    "https://acmetools.example/": ("text/html", ACME_HOME),
    "https://acmetools.example/contact": ("text/html", ACME_CONTACT),
    "https://acmetools.example/wholesale-program": ("text/html", ACME_WHOLESALE),
    "https://acmetools.example/robots.txt": ("text/plain", "User-agent: *\nDisallow: /private/\n"),
    "https://drillblog.example/best-drills": ("text/html", BLOG),
    "https://beanbulk.example/": ("text/html", BEAN),
    "https://norobots.example/robots.txt": ("text/plain", "User-agent: *\nDisallow: /\n"),
    "https://norobots.example/": ("text/html", "<html><body>Wholesale cordless drill supplier</body></html>"),
    "https://pdfonly.example/catalog": ("application/pdf", "%PDF-1.4"),
}


def web_transport(sites: dict[str, tuple[str, str]] | None = None, log: list[str] | None = None) -> httpx.MockTransport:
    pages = sites or SITES

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if log is not None:
            log.append(url)
        if url.rstrip("/") == "https://redirect.example":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
        if url not in pages:
            return httpx.Response(404, text="not found")
        kind, body = pages[url]
        return httpx.Response(200, headers={"content-type": f"{kind}; charset=utf-8"}, text=body)

    return httpx.MockTransport(handler)


def web_client(sites=None, log=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=web_transport(sites, log), follow_redirects=False)


class FakeSearch(SearchProvider):
    name = "fake"

    def __init__(self, urls: list[str] | None = None, error: Exception | None = None) -> None:
        super().__init__()
        self.urls = urls if urls is not None else [
            "https://www.amazon.com/dp/B0CHWRXH8B", "https://acmetools.example/", "https://drillblog.example/best-drills",
            "https://norobots.example/", "https://beanbulk.example/", "https://pdfonly.example/catalog",
        ]
        self.error = error
        self.queries: list[str] = []

    async def search(self, query: str, count: int) -> list[Hit]:
        self.queries.append(query)
        if self.error:
            raise self.error
        return [Hit(u, "result", "") for u in self.urls][:count]


def acme_profile(**overrides) -> dict:
    data = {
        "company_name": "Acme Tool Wholesale", "business_type": "wholesaler", "is_supplier": True, "relevance": 0.9,
        "summary": "Wholesale distributor of cordless drills for resellers.", "categories": ["tools"],
        "products": [
            {"name": "DeWalt 20V MAX drill kits", "quote": "DeWalt 20V MAX cordless drill kits in case packs of 10."},
            {"name": "Makita impact drivers", "quote": "Makita impact drivers in stock now"},  # not on the site
        ],
        "signals": ["Minimum order: 50 units.", "Net 30 terms for approved resellers."],
        "moq": "Minimum order: 50 units.", "price_notes": "Wholesale pricing from $65 per unit.",
        "location": "Chicago, IL", "contact_email": "sales@acmetools.example", "contact_phone": "(312) 555-0100",
    }
    data.update(overrides)
    return data
