"""
Domain filtering for web search results.

Applies allowed_domains and blocked_domains filtering as a post-processing step,
since not all search providers fully support domain filtering natively.
"""
import logging
from typing import List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class DomainFilter:
    """Filters search results based on allowed and blocked domain lists."""

    def __init__(
        self,
        allowed_domains: Optional[List[str]] = None,
        blocked_domains: Optional[List[str]] = None,
    ):
        self.allowed_domains = allowed_domains or []
        self.blocked_domains = blocked_domains or []

    def filter_results(self, results: list) -> list:
        """
        Filter search results by domain rules.

        Args:
            results: List of SearchResult objects

        Returns:
            Filtered list of SearchResult objects
        """
        if not self.allowed_domains and not self.blocked_domains:
            return results

        filtered = []
        for result in results:
            domain = self._extract_domain(result.url)
            if not domain:
                continue

            # Check blocked domains first
            if self.blocked_domains and self._matches_any(domain, self.blocked_domains):
                logger.debug(f"[DomainFilter] Blocked: {result.url}")
                continue

            # Check allowed domains
            if self.allowed_domains and not self._matches_any(domain, self.allowed_domains):
                logger.debug(f"[DomainFilter] Not in allowed list: {result.url}")
                continue

            filtered.append(result)

        logger.info(
            f"[DomainFilter] Filtered {len(results)} -> {len(filtered)} results "
            f"(allowed={self.allowed_domains}, blocked={self.blocked_domains})"
        )
        return filtered

    @staticmethod
    def _extract_domain(url: str) -> Optional[str]:
        """Extract domain from URL."""
        try:
            parsed = urlparse(url)
            return parsed.netloc.lower()
        except Exception:
            return None

    @staticmethod
    def _matches_any(domain: str, patterns: List[str]) -> bool:
        """
        Check if domain matches any pattern.

        Supports:
        - Exact match: "example.com"
        - Subdomain match: "docs.example.com" matches "example.com"
        """
        for pattern in patterns:
            pattern = pattern.lower().strip()
            if domain == pattern or domain.endswith("." + pattern):
                return True
        return False
