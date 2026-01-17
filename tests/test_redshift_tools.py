"""
Tests for Redshift tools.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from combined_mcp_server.redshift.s3_storage import compute_query_hash, ResultStorage


class TestQueryHash:
    """Tests for query hashing."""

    def test_consistent_hash(self):
        """Test same query produces same hash."""
        query = "SELECT * FROM users"
        hash1 = compute_query_hash(query)
        hash2 = compute_query_hash(query)
        assert hash1 == hash2

    def test_different_queries_different_hash(self):
        """Test different queries produce different hashes."""
        hash1 = compute_query_hash("SELECT * FROM users")
        hash2 = compute_query_hash("SELECT * FROM orders")
        assert hash1 != hash2

    def test_hash_length(self):
        """Test hash is 8 characters."""
        hash_val = compute_query_hash("test query")
        assert len(hash_val) == 8


class TestResultStorage:
    """Tests for S3 result storage."""

    def test_store_results(self, mock_s3_client):
        """Test storing query results in S3."""
        with patch(
            "combined_mcp_server.redshift.s3_storage.get_s3_client",
            return_value=MagicMock(
                upload_json=MagicMock(return_value="s3://bucket/key.json.gz"),
                generate_presigned_url=MagicMock(return_value="https://presigned.url"),
            ),
        ):
            storage = ResultStorage()
            
            result = storage.store_results(
                query="SELECT * FROM test",
                columns=["id", "name"],
                rows=[{"id": 1, "name": "test"}],
            )
            
            assert result.s3_uri.startswith("s3://")
            assert result.row_count == 1
            assert result.presigned_url is not None


class TestRedshiftTools:
    """Tests for Redshift MCP tools."""

    @pytest.mark.asyncio
    async def test_run_query_small_result(self):
        """Test run_query with small result set."""
        with patch(
            "combined_mcp_server.redshift.tools.get_redshift_connection_manager"
        ) as mock_manager:
            from combined_mcp_server.redshift.connection import QueryResult
            
            mock_conn = MagicMock()
            mock_conn.execute_query.return_value = QueryResult(
                columns=["id", "name"],
                rows=[{"id": 1, "name": "test"}],
                row_count=1,
                execution_time_ms=10.5,
                truncated=False,
            )
            mock_manager.return_value = mock_conn
            
            from combined_mcp_server.redshift.tools import run_query
            
            result = await run_query(
                sql="SELECT * FROM test",
                db_user="testuser",
            )
            
            assert result["success"] is True
            assert result["row_count"] == 1
            assert not result["is_sample"]
            assert len(result["rows"]) == 1

    @pytest.mark.asyncio
    async def test_run_query_large_result(self):
        """Test run_query with large result set stores to S3."""
        with patch(
            "combined_mcp_server.redshift.tools.get_redshift_connection_manager"
        ) as mock_manager, patch(
            "combined_mcp_server.redshift.tools.get_result_storage"
        ) as mock_storage:
            from combined_mcp_server.redshift.connection import QueryResult
            from combined_mcp_server.redshift.s3_storage import StoredResult
            
            # Large result set
            rows = [{"id": i, "name": f"test{i}"} for i in range(150)]
            
            mock_conn = MagicMock()
            mock_conn.execute_query.return_value = QueryResult(
                columns=["id", "name"],
                rows=rows,
                row_count=150,
                execution_time_ms=50.0,
                truncated=False,
            )
            mock_manager.return_value = mock_conn
            
            mock_storage.return_value.store_results.return_value = StoredResult(
                s3_uri="s3://bucket/results.json.gz",
                s3_key="results.json.gz",
                bucket="bucket",
                row_count=150,
                size_bytes=1000,
                presigned_url="https://presigned.url",
            )
            
            from combined_mcp_server.redshift.tools import run_query
            
            result = await run_query(
                sql="SELECT * FROM test",
                db_user="testuser",
            )
            
            assert result["success"] is True
            assert result["row_count"] == 150
            assert result["is_sample"] is True
            assert len(result["rows"]) == 20  # Sample size
            assert "s3_path" in result
            assert "s3_download_url" in result
