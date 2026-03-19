"""Web search provider module."""

from app.services.web_search.providers import (
    SearchProvider,
    SearchResult,
    TavilySearchProvider,
    BraveSearchProvider,
    create_search_provider,
)
from app.services.web_search.domain_filter import DomainFilter
