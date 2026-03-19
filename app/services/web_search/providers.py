"""
Search provider interface and implementations.

Supports Tavily (recommended for AI) and Brave Search as providers.
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Standardized search result from any provider."""
    url: str
    title: str
    content: str  # Page content/snippet
    page_age: Optional[str] = None


class SearchProvider(ABC):
    """Abstract search provider interface."""

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 5,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
        user_location: Optional[dict] = None,
    ) -> List[SearchResult]:
        """
        Execute a web search.

        Args:
            query: Search query string
            max_results: Maximum number of results to return
            allowed_domains: Only include results from these domains
            blocked_domains: Exclude results from these domains
            user_location: Optional location dict for localized results

        Returns:
            List of SearchResult objects
        """
        pass


class TavilySearchProvider(SearchProvider):
    """
    Tavily search provider.

    Tavily is designed for AI applications and returns clean, structured content.
    Uses the tavily-python SDK.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = None

    @property
    def client(self):
        """Lazy-initialize Tavily client."""
        if self._client is None:
            from tavily import TavilyClient
            self._client = TavilyClient(api_key=self.api_key)
        return self._client

    async def search(
        self,
        query: str,
        max_results: int = 5,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
        user_location: Optional[dict] = None,
    ) -> List[SearchResult]:
        """Execute search via Tavily API."""
        import asyncio

        kwargs = {
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",
            "include_raw_content": False,
        }

        if allowed_domains:
            kwargs["include_domains"] = allowed_domains
        if blocked_domains:
            kwargs["exclude_domains"] = blocked_domains

        logger.info(f"[WebSearch/Tavily] Searching: {query!r} (max_results={max_results})")

        # Tavily SDK is synchronous, run in thread pool
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: self.client.search(**kwargs))

        results = []
        for item in response.get("results", []):
            results.append(SearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                content=item.get("content", ""),
                page_age=item.get("published_date"),
            ))

        logger.info(f"[WebSearch/Tavily] Got {len(results)} results")
        return results


class BraveSearchProvider(SearchProvider):
    """
    Brave Search provider.

    Uses the Brave Search API via httpx.
    """

    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client: Optional[Any] = None

    @property
    def client(self):
        """Lazy-initialize httpx client for connection reuse."""
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def search(
        self,
        query: str,
        max_results: int = 5,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
        user_location: Optional[dict] = None,
    ) -> List[SearchResult]:
        """Execute search via Brave Search API."""
        # Build query with domain filtering via site: prefix
        search_query = query
        if allowed_domains:
            site_filter = " OR ".join(f"site:{d}" for d in allowed_domains)
            search_query = f"({site_filter}) {query}"

        params = {
            "q": search_query,
            "count": max_results,
        }

        if user_location and user_location.get("country"):
            params["country"] = user_location["country"]

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }

        logger.info(f"[WebSearch/Brave] Searching: {search_query!r} (count={max_results})")

        response = await self.client.get(self.ENDPOINT, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()

        # Domain filtering is handled by DomainFilter post-processing
        results = []
        for item in data.get("web", {}).get("results", []):
            results.append(SearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                content=item.get("description", ""),
                page_age=item.get("page_age"),
            ))

        logger.info(f"[WebSearch/Brave] Got {len(results)} results")
        return results[:max_results]


def create_search_provider(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
) -> SearchProvider:
    """
    Create a search provider instance based on configuration.

    Args:
        provider: Provider name ('tavily' or 'brave'). Defaults to settings.
        api_key: API key. Defaults to settings.

    Returns:
        SearchProvider instance

    Raises:
        ValueError: If provider is unknown or API key is missing
    """
    provider = provider or settings.web_search_provider
    api_key = api_key or settings.web_search_api_key

    if not api_key:
        raise ValueError(
            f"Web search API key is required. Set WEB_SEARCH_API_KEY environment variable."
        )

    if provider == "tavily":
        return TavilySearchProvider(api_key=api_key)
    elif provider == "brave":
        return BraveSearchProvider(api_key=api_key)
    else:
        raise ValueError(f"Unknown search provider: {provider}. Use 'tavily' or 'brave'.")
