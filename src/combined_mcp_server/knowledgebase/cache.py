"""
FIFO query cache for vectorstore.

Caches query results to avoid redundant database lookups.
"""

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from combined_mcp_server.config import get_settings
from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CacheEntry:
    """Cache entry with query result and metadata."""

    results: list[dict[str, Any]]
    query: str
    top_k: int
    search_type: str
    hit_count: int = 0


class QueryCache:
    """
    Thread-safe FIFO query cache for vectorstore results.

    Caches query results using a hash of (query, top_k, search_type).
    Evicts oldest entries when the cache reaches its size limit.
    """

    def __init__(self, max_size: int | None = None) -> None:
        """
        Initialize query cache.

        Args:
            max_size: Maximum number of entries (uses config default if not provided)
        """
        settings = get_settings()
        self._max_size = max_size or settings.query_cache.size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

        logger.info("Query cache initialized", max_size=self._max_size)

    @staticmethod
    def _generate_key(query: str, top_k: int, search_type: str) -> str:
        """
        Generate cache key from query parameters.

        Args:
            query: Search query
            top_k: Number of results requested
            search_type: Type of search (semantic, keyword, hybrid)

        Returns:
            Cache key string
        """
        key_string = f"{query}:{top_k}:{search_type}"
        return hashlib.sha256(key_string.encode()).hexdigest()

    def get(
        self,
        query: str,
        top_k: int,
        search_type: str,
    ) -> list[dict[str, Any]] | None:
        """
        Get cached results for a query.

        Args:
            query: Search query
            top_k: Number of results requested
            search_type: Type of search

        Returns:
            Cached results or None if not found
        """
        key = self._generate_key(query, top_k, search_type)

        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                entry.hit_count += 1
                self._hits += 1

                # Move to end (most recently used)
                self._cache.move_to_end(key)

                logger.debug(
                    "Cache hit",
                    query_preview=query[:50],
                    hit_count=entry.hit_count,
                )
                return entry.results

            self._misses += 1
            logger.debug("Cache miss", query_preview=query[:50])
            return None

    def put(
        self,
        query: str,
        top_k: int,
        search_type: str,
        results: list[dict[str, Any]],
    ) -> None:
        """
        Store results in cache.

        Args:
            query: Search query
            top_k: Number of results requested
            search_type: Type of search
            results: Query results to cache
        """
        key = self._generate_key(query, top_k, search_type)

        with self._lock:
            # Remove oldest entry if at capacity
            if len(self._cache) >= self._max_size:
                oldest_key, oldest_entry = self._cache.popitem(last=False)
                logger.debug(
                    "Cache eviction (FIFO)",
                    evicted_query_preview=oldest_entry.query[:50],
                )

            # Add new entry
            self._cache[key] = CacheEntry(
                results=results,
                query=query,
                top_k=top_k,
                search_type=search_type,
            )

            logger.debug(
                "Cache put",
                query_preview=query[:50],
                cache_size=len(self._cache),
            )

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            size = len(self._cache)
            self._cache.clear()
            logger.info("Cache cleared", cleared_entries=size)

    def get_stats(self) -> dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = self._hits / total_requests if total_requests > 0 else 0.0

            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": hit_rate,
                "total_requests": total_requests,
            }


# Singleton instance
_query_cache: QueryCache | None = None


def get_query_cache() -> QueryCache:
    """Get query cache singleton."""
    global _query_cache
    if _query_cache is None:
        _query_cache = QueryCache()
    return _query_cache
