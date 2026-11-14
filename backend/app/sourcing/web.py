"""A polite, defensive crawler for supplier websites.

- Only http(s), only public internet addresses: every hop of every redirect is resolved and refused if it points
  at a private, loopback, link-local or otherwise non-global address (protection against server-side request
  forgery into the platform's own network).
- robots.txt is honoured with our own user agent, pages are size- and time-limited, only HTML is read, and a site
  gets at most a few pages: the page the search found plus its contact / about / wholesale pages.
- Nothing is executed: pages are parsed as text.
"""
import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings

settings = get_settings()

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
INTERESTING_LINK = re.compile(r"contact|about|wholesale|bulk|liquidat|b2b|trade|dealer|distribut|reseller", re.I)
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
              "header", "footer", "address", "table", "ul", "ol"}

# Marketplaces, social networks and reference sites: never suppliers' own websites, and many forbid crawling.
BLOCKED_DOMAINS = (
    "amazon.", "ebay.", "walmart.", "target.com", "bestbuy.com", "homedepot.com", "lowes.com", "costco.com",
    "alibaba.com", "aliexpress.", "temu.com", "etsy.com", "wish.com", "shein.", "facebook.com", "instagram.com",
    "youtube.com", "tiktok.com", "twitter.com", "x.com", "linkedin.com", "pinterest.", "reddit.com", "quora.com",
    "wikipedia.org", "yelp.com", "bbb.org", "google.", "bing.com", "yahoo.com", "duckduckgo.com", "craigslist.org",
    "medium.com", "nytimes.com", "forbes.com",
)


class FetchError(Exception):
    pass


@dataclass
class Page:
    url: str
    title: str = ""
    site_name: str = ""
    description: str = ""
    text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)  # (absolute url, link text)
    emails: list[str] = field(default_factory=list)  # published on the page (text or mailto links)
    phones: list[str] = field(default_factory=list)


class _Parser(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.page = Page(url=base)
        self._skip = 0
        self._in_title = False
        self._link: tuple[str, list[str]] | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        attr = {k: (v or "") for k, v in attrs}
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (attr.get("property") or attr.get("name") or "").lower()
            if name == "og:site_name":
                self.page.site_name = attr.get("content", "")[:200]
            elif name in ("description", "og:description") and not self.page.description:
                self.page.description = attr.get("content", "")[:500]
        elif tag == "a":
            href = attr.get("href", "").strip()
            if href.lower().startswith("mailto:"):
                address = href[7:].split("?")[0].strip()
                if EMAIL_RE.fullmatch(address):
                    self.page.emails.append(address.lower())
            elif href.lower().startswith("tel:"):
                self.page.phones.append(href[4:].strip()[:40])
            elif href and not href.startswith(("#", "javascript:")):
                self._link = (urljoin(self.base, href), [])
        if tag in BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._link:
            url, words = self._link
            self.page.links.append((url, " ".join(words).strip()[:120]))
            self._link = None
        if tag in BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.page.title += data
            return
        self._chunks.append(data)
        if self._link is not None:
            self._link[1].append(data.strip())

    def result(self) -> Page:
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self._chunks))
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        self.page.text = text[:40000]
        self.page.title = re.sub(r"\s+", " ", self.page.title).strip()[:200]
        for email in EMAIL_RE.findall(text):
            if email.lower() not in self.page.emails and not email.lower().endswith((".png", ".jpg", ".gif", ".webp")):
                self.page.emails.append(email.lower())
        for phone in PHONE_RE.findall(text):
            if phone not in self.page.phones:
                self.page.phones.append(phone.strip())
        self.page.emails = self.page.emails[:10]
        self.page.phones = self.page.phones[:5]
        return self.page


def parse_html(url: str, html: str) -> Page:
    parser = _Parser(url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed markup: keep whatever was read
        pass
    return parser.result()


def registered_domain(url_or_host: str) -> str:
    host = urlparse(url_or_host).hostname if "://" in url_or_host else url_or_host
    host = (host or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def blocked(url: str) -> bool:
    domain = registered_domain(url)
    for entry in BLOCKED_DOMAINS:
        if entry.endswith("."):  # a brand on any country domain: amazon.com, amazon.co.uk, smile.amazon.de
            if re.search(rf"(^|\.){re.escape(entry)}", domain):
                return True
        elif domain == entry or domain.endswith("." + entry):
            return True
    return False


async def _public_host(host: str) -> None:
    if settings.web_allow_private_hosts:
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise FetchError(f"{host} resolves to a non-public address")


class Crawler:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}

    async def __aenter__(self) -> "Crawler":
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.web_fetch_timeout_seconds, follow_redirects=False,
                headers={"User-Agent": f"{settings.crawler_user_agent} (+{settings.app_base_url}/bot)",
                         "Accept": "text/html,application/xhtml+xml"},
            )
            self._owns = True
        else:
            self._owns = False
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns and self._client is not None:
            await self._client.aclose()

    async def _get(self, url: str, *, html_only: bool = True) -> tuple[str, str]:
        """GET with manual, re-checked redirects. Returns (final url, body text)."""
        assert self._client is not None
        for _ in range(4):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise FetchError("only http(s) URLs are fetched")
            await _public_host(parsed.hostname)
            try:
                async with self._client.stream("GET", url) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code >= 400:
                        raise FetchError(f"HTTP {response.status_code}")
                    kind = response.headers.get("content-type", "")
                    if html_only and "html" not in kind:
                        raise FetchError(f"not HTML ({kind or 'unknown type'})")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > settings.web_fetch_max_bytes:
                            break
                    return url, bytes(body).decode(response.encoding or "utf-8", errors="replace")
            except httpx.HTTPError as exc:
                raise FetchError(type(exc).__name__) from exc
        raise FetchError("too many redirects")

    async def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            rules: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
            try:
                _, body = await self._get(origin + "/robots.txt", html_only=False)
                rules.parse(body.splitlines())
            except FetchError:
                rules = None  # no robots.txt (or unreachable): crawling is allowed
            self._robots[origin] = rules
        rules = self._robots[origin]
        return rules is None or rules.can_fetch(settings.crawler_user_agent, url)

    async def page(self, url: str) -> Page:
        if blocked(url):
            raise FetchError("marketplace or social site")
        if not await self.allowed(url):
            raise FetchError("disallowed by robots.txt")
        final, body = await self._get(url)
        return parse_html(final, body)

    async def site(self, url: str) -> list[Page]:
        """The page found by search plus up to a few contact/about/wholesale pages on the same site."""
        first = await self.page(url)
        pages = [first]
        domain = registered_domain(first.url)
        seen = {first.url.split("#")[0]}
        for link, text in first.links:
            if len(pages) >= settings.web_sourcing_pages_per_site:
                break
            target = link.split("#")[0]
            if target in seen or registered_domain(target) != domain:
                continue
            if INTERESTING_LINK.search(text) or INTERESTING_LINK.search(urlparse(target).path):
                seen.add(target)
                try:
                    pages.append(await self.page(target))
                except FetchError:
                    continue
        return pages
