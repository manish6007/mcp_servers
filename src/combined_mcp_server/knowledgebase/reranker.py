"""
Reciprocal Rank Fusion (RRF) reranker.

Combines rankings from multiple search methods into a unified ranking.
"""

from dataclasses import dataclass
from typing import Any

from combined_mcp_server.config import get_settings
from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RankedResult:
    """A search result with ranking information."""

    id: int
    content: str
    metadata: dict[str, Any]
    schema_toon: str | None = None  # TOON-encoded schema for Text2SQL
    semantic_rank: int | None = None
    keyword_rank: int | None = None
    rrf_score: float = 0.0
    semantic_score: float | None = None
    keyword_score: float | None = None


class RRFReranker:
    """
    Reciprocal Rank Fusion reranker.

    Combines results from semantic and keyword search using the RRF formula:
    score = (semantic_weight / (k + semantic_rank)) + (keyword_weight / (k + keyword_rank))

    This produces a unified ranking that balances both search methods.
    """

    def __init__(
        self,
        semantic_weight: float | None = None,
        keyword_weight: float | None = None,
        k: int | None = None,
    ) -> None:
        """
        Initialize RRF reranker.

        Args:
            semantic_weight: Weight for semantic search results (0-1)
            keyword_weight: Weight for keyword search results (0-1)
            k: RRF constant (higher = more balanced between ranks)
        """
        settings = get_settings()

        self._semantic_weight = semantic_weight or settings.hybrid_search.semantic_weight
        self._keyword_weight = keyword_weight or settings.hybrid_search.keyword_weight
        self._k = k or settings.hybrid_search.rrf_k

        logger.info(
            "RRF reranker initialized",
            semantic_weight=self._semantic_weight,
            keyword_weight=self._keyword_weight,
            k=self._k,
        )

    def fuse(
        self,
        semantic_results: list[dict[str, Any]],
        keyword_results: list[dict[str, Any]],
        top_k: int | None = None,
    ) -> list[RankedResult]:
        """
        Fuse semantic and keyword search results using RRF.

        Args:
            semantic_results: Results from semantic (vector) search
                Each should have 'id', 'content', 'metadata', optionally 'score'
            keyword_results: Results from keyword (FTS) search
                Each should have 'id', 'content', 'metadata', optionally 'score'
            top_k: Maximum number of results to return

        Returns:
            List of RankedResult sorted by RRF score (descending)
        """
        # Build lookup by ID
        results_by_id: dict[int, RankedResult] = {}

        # Process semantic results
        for rank, result in enumerate(semantic_results, start=1):
            doc_id = result["id"]
            if doc_id not in results_by_id:
                results_by_id[doc_id] = RankedResult(
                    id=doc_id,
                    content=result["content"],
                    metadata=result.get("metadata", {}),
                    schema_toon=result.get("schema_toon"),
                )
            results_by_id[doc_id].semantic_rank = rank
            results_by_id[doc_id].semantic_score = result.get("score")

        # Process keyword results
        for rank, result in enumerate(keyword_results, start=1):
            doc_id = result["id"]
            if doc_id not in results_by_id:
                results_by_id[doc_id] = RankedResult(
                    id=doc_id,
                    content=result["content"],
                    metadata=result.get("metadata", {}),
                    schema_toon=result.get("schema_toon"),
                )
            results_by_id[doc_id].keyword_rank = rank
            results_by_id[doc_id].keyword_score = result.get("score")

        # Calculate RRF scores
        for result in results_by_id.values():
            semantic_contribution = 0.0
            keyword_contribution = 0.0

            if result.semantic_rank is not None:
                semantic_contribution = self._semantic_weight / (
                    self._k + result.semantic_rank
                )

            if result.keyword_rank is not None:
                keyword_contribution = self._keyword_weight / (
                    self._k + result.keyword_rank
                )

            result.rrf_score = semantic_contribution + keyword_contribution

        # Sort by RRF score (descending)
        ranked = sorted(
            results_by_id.values(),
            key=lambda r: r.rrf_score,
            reverse=True,
        )

        # Apply top_k limit
        if top_k is not None:
            ranked = ranked[:top_k]

        logger.debug(
            "RRF fusion completed",
            semantic_count=len(semantic_results),
            keyword_count=len(keyword_results),
            fused_count=len(ranked),
        )

        return ranked

    def to_dict_list(self, results: list[RankedResult]) -> list[dict[str, Any]]:
        """
        Convert RankedResult list to dictionary list.

        Args:
            results: List of RankedResult objects

        Returns:
            List of dictionaries suitable for API response
        """
        return [
            {
                "id": r.id,
                "content": r.content,
                "metadata": r.metadata,
                "schema_toon": r.schema_toon,
                "score": r.rrf_score,
                "semantic_rank": r.semantic_rank,
                "keyword_rank": r.keyword_rank,
                "semantic_score": r.semantic_score,
                "keyword_score": r.keyword_score,
            }
            for r in results
        ]


# Singleton instance
_reranker: RRFReranker | None = None


def get_reranker() -> RRFReranker:
    """Get RRF reranker singleton."""
    global _reranker
    if _reranker is None:
        _reranker = RRFReranker()
    return _reranker
