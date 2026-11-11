"""Web search providers. The engine never scrapes a search engine's result pages (that breaks their terms and gets
blocked); it uses a search API.

- searxng: a self-hosted SearXNG instance (docker compose --profile search). No third-party account needed.
- brave: the Brave Search API (BRAVE_API_KEY).
"""
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

settings = get_settings()


class SearchError(Exception):
    pass


@dataclass
class Hit:
    url: str
    title: str
    snippet: str


class SearchProvider:
    name = "base"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def _get(self, url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=15)
        try:
            response = await client.get(url, params=params, headers=headers or {})
        except httpx.HTTPError as exc:
            raise SearchError(f"{self.name} unreachable: {type(exc).__name__}") from exc
        finally:
            if owns:
                await client.aclose()
        if response.status_code >= 400:
            raise SearchError(f"{self.name} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SearchError(f"{self.name} returned a non-JSON response") from exc

    async def search(self, query: str, count: int) -> list[Hit]:  # pragma: no cover - interface
        raise NotImplementedError


class SearxngProvider(SearchProvider):
    name = "searxng"

    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client)
        self.base_url = base_url.rstrip("/")

    async def search(self, query: str, count: int) -> list[Hit]:
        data = await self._get(f"{self.base_url}/search", {"q": query, "format": "json", "categories": "general",
                                                            "language": "en-US", "safesearch": 1})
        return [Hit(r.get("url", ""), r.get("title", ""), r.get("content", ""))
                for r in (data.get("results") or [])[:count] if r.get("url")]


class BraveProvider(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        super().__init__(client)
        self.api_key = api_key

    async def search(self, query: str, count: int) -> list[Hit]:
        data = await self._get("https://api.search.brave.com/res/v1/web/search",
                               {"q": query, "count": min(count, 20), "country": "us", "safesearch": "moderate"},
                               {"X-Subscription-Token": self.api_key, "Accept": "application/json"})
        results = (data.get("web") or {}).get("results") or []
        return [Hit(r.get("url", ""), r.get("title", ""), r.get("description", "")) for r in results if r.get("url")]


_provider: SearchProvider | None = None
_configured = False


def get_search() -> SearchProvider | None:
    global _provider, _configured
    if not _configured:
        if settings.search_provider == "searxng" and settings.searxng_url:
            _provider = SearxngProvider(settings.searxng_url)
        elif settings.search_provider == "brave" and settings.brave_api_key:
            _provider = BraveProvider(settings.brave_api_key)
        _configured = True
    return _provider


def set_search(provider: SearchProvider | None) -> None:
    """Tests install a fake here; None restores the configured provider."""
    global _provider, _configured
    _provider, _configured = provider, provider is not None
