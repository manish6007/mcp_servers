"""
Tests for knowledgebase tools.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from combined_mcp_server.knowledgebase.cache import QueryCache
from combined_mcp_server.knowledgebase.reranker import RRFReranker


class TestQueryCache:
    """Tests for FIFO query cache."""

    def test_cache_hit(self):
        """Test cache returns stored results."""
        cache = QueryCache(max_size=5)
        
        # Store result
        results = [{"id": 1, "content": "test"}]
        cache.put("test query", 10, "hybrid", results)
        
        # Retrieve
        cached = cache.get("test query", 10, "hybrid")
        assert cached == results

    def test_cache_miss(self):
        """Test cache returns None for missing queries."""
        cache = QueryCache(max_size=5)
        
        cached = cache.get("nonexistent", 10, "hybrid")
        assert cached is None

    def test_fifo_eviction(self):
        """Test oldest entries are evicted when cache is full."""
        cache = QueryCache(max_size=3)
        
        # Fill cache
        cache.put("query1", 10, "hybrid", [{"id": 1}])
        cache.put("query2", 10, "hybrid", [{"id": 2}])
        cache.put("query3", 10, "hybrid", [{"id": 3}])
        
        # Add one more - should evict query1
        cache.put("query4", 10, "hybrid", [{"id": 4}])
        
        assert cache.get("query1", 10, "hybrid") is None
        assert cache.get("query2", 10, "hybrid") is not None
        assert cache.get("query4", 10, "hybrid") is not None

    def test_cache_key_uniqueness(self):
        """Test different parameters create different cache entries."""
        cache = QueryCache(max_size=10)
        
        # Same query, different parameters
        cache.put("test", 5, "hybrid", [{"id": 1}])
        cache.put("test", 10, "hybrid", [{"id": 2}])
        cache.put("test", 10, "semantic", [{"id": 3}])
        
        assert cache.get("test", 5, "hybrid") == [{"id": 1}]
        assert cache.get("test", 10, "hybrid") == [{"id": 2}]
        assert cache.get("test", 10, "semantic") == [{"id": 3}]

    def test_cache_stats(self):
        """Test cache statistics tracking."""
        cache = QueryCache(max_size=5)
        
        # Miss
        cache.get("query1", 10, "hybrid")
        
        # Store and hit
        cache.put("query1", 10, "hybrid", [{"id": 1}])
        cache.get("query1", 10, "hybrid")
        cache.get("query1", 10, "hybrid")
        
        stats = cache.get_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["size"] == 1


class TestRRFReranker:
    """Tests for RRF reranking."""

    def test_rrf_fusion_basic(self):
        """Test basic RRF fusion of two result sets."""
        reranker = RRFReranker(semantic_weight=0.5, keyword_weight=0.5, k=60)
        
        semantic = [
            {"id": 1, "content": "doc1", "metadata": {}, "score": 0.9},
            {"id": 2, "content": "doc2", "metadata": {}, "score": 0.8},
        ]
        keyword = [
            {"id": 2, "content": "doc2", "metadata": {}, "score": 0.95},
            {"id": 3, "content": "doc3", "metadata": {}, "score": 0.7},
        ]
        
        results = reranker.fuse(semantic, keyword)
        
        # doc2 appears in both, should have highest score
        assert results[0].id == 2
        
        # All 3 documents should be in results
        result_ids = {r.id for r in results}
        assert result_ids == {1, 2, 3}

    def test_rrf_top_k(self):
        """Test RRF respects top_k limit."""
        reranker = RRFReranker()
        
        semantic = [{"id": i, "content": f"doc{i}", "metadata": {}} for i in range(10)]
        keyword = [{"id": i + 5, "content": f"doc{i+5}", "metadata": {}} for i in range(10)]
        
        results = reranker.fuse(semantic, keyword, top_k=5)
        
        assert len(results) == 5

    def test_rrf_with_weights(self):
        """Test RRF with different weights."""
        # Heavy semantic weight
        reranker_semantic = RRFReranker(semantic_weight=0.9, keyword_weight=0.1, k=60)
        
        semantic = [{"id": 1, "content": "doc1", "metadata": {}}]
        keyword = [{"id": 2, "content": "doc2", "metadata": {}}]
        
        results = reranker_semantic.fuse(semantic, keyword)
        
        # Document 1 should rank higher due to semantic weight
        assert results[0].id == 1

    def test_rrf_to_dict_list(self):
        """Test conversion to dictionary list."""
        reranker = RRFReranker()
        
        semantic = [{"id": 1, "content": "test", "metadata": {"key": "value"}}]
        keyword = []
        
        results = reranker.fuse(semantic, keyword)
        dict_results = reranker.to_dict_list(results)
        
        assert isinstance(dict_results, list)
        assert dict_results[0]["id"] == 1
        assert dict_results[0]["content"] == "test"
        assert dict_results[0]["metadata"] == {"key": "value"}
        assert "score" in dict_results[0]
