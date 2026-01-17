"""
S3 storage for large Redshift query results.

Handles storing query results that exceed the row limit.
"""

import hashlib
from dataclasses import dataclass
from typing import Any

from combined_mcp_server.config import get_settings
from combined_mcp_server.utils.logging import get_logger
from combined_mcp_server.utils.s3 import S3Client, generate_result_key, get_s3_client

logger = get_logger(__name__)


@dataclass
class StoredResult:
    """Information about a stored result set."""

    s3_uri: str
    s3_key: str
    bucket: str
    row_count: int
    size_bytes: int
    presigned_url: str | None = None


def compute_query_hash(query: str) -> str:
    """
    Compute a short hash of a query for identification.

    Args:
        query: SQL query string

    Returns:
        8-character hash string
    """
    return hashlib.sha256(query.encode()).hexdigest()[:8]


class ResultStorage:
    """
    Manages S3 storage for large query results.

    When query results exceed the configured row limit, this class
    handles storing the full result set in S3 and generating
    presigned URLs for access.
    """

    def __init__(self, s3_client: S3Client | None = None) -> None:
        """
        Initialize result storage.

        Args:
            s3_client: Optional S3 client instance (uses default if not provided)
        """
        self._s3_client = s3_client or get_s3_client()
        self._settings = get_settings()

    def store_results(
        self,
        query: str,
        columns: list[str],
        rows: list[dict[str, Any]],
        generate_url: bool = True,
        url_expiration_seconds: int = 3600,
    ) -> StoredResult:
        """
        Store query results in S3.

        Args:
            query: Original SQL query (for identification)
            columns: Column names
            rows: Result rows as dictionaries
            generate_url: Whether to generate a presigned URL
            url_expiration_seconds: Presigned URL expiration time

        Returns:
            StoredResult with S3 location and access information
        """
        query_hash = compute_query_hash(query)
        bucket = self._settings.redshift.results_bucket
        prefix = self._settings.redshift.results_prefix
        key = generate_result_key(prefix, query_hash)

        # Prepare result data
        result_data = {
            "query": query,
            "query_hash": query_hash,
            "columns": columns,
            "row_count": len(rows),
            "rows": rows,
        }

        logger.info(
            "Storing query results in S3",
            bucket=bucket,
            key=key,
            row_count=len(rows),
        )

        # Upload to S3 (compressed)
        s3_uri = self._s3_client.upload_json(
            bucket=bucket,
            key=key,
            data=result_data,
            compress=True,
        )

        # Generate presigned URL if requested
        presigned_url = None
        if generate_url:
            # Key will have .gz extension after upload
            actual_key = key if key.endswith(".gz") else f"{key}.gz"
            presigned_url = self._s3_client.generate_presigned_url(
                bucket=bucket,
                key=actual_key,
                expiration_seconds=url_expiration_seconds,
            )

        stored_result = StoredResult(
            s3_uri=s3_uri,
            s3_key=key if key.endswith(".gz") else f"{key}.gz",
            bucket=bucket,
            row_count=len(rows),
            size_bytes=0,  # Would need additional call to get actual size
            presigned_url=presigned_url,
        )

        logger.info(
            "Query results stored successfully",
            s3_uri=s3_uri,
            row_count=len(rows),
        )

        return stored_result


# Singleton instance
_result_storage: ResultStorage | None = None


def get_result_storage() -> ResultStorage:
    """Get result storage singleton."""
    global _result_storage
    if _result_storage is None:
        _result_storage = ResultStorage()
    return _result_storage
