"""
Redshift connection manager using IAM-based authentication.

Uses boto3 get_cluster_credentials API for temporary credentials.
"""

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import boto3
import psycopg
from botocore.exceptions import ClientError
from psycopg import Connection, sql
from psycopg.rows import dict_row

from combined_mcp_server.config import get_settings
from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)


class RedshiftConnectionError(Exception):
    """Custom exception for Redshift connection errors."""

    pass


class RedshiftQueryError(Exception):
    """Custom exception for Redshift query errors."""

    pass


@dataclass
class RedshiftCredentials:
    """Temporary Redshift credentials."""

    db_user: str
    db_password: str
    expiration: float  # Unix timestamp


@dataclass
class QueryResult:
    """Result of a Redshift query execution."""

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float
    truncated: bool = False


class RedshiftConnectionManager:
    """
    Manages Redshift connections using IAM-based authentication.

    Uses get_cluster_credentials API to obtain temporary credentials
    for each connection, ensuring secure and auditable access.
    """

    # Credential cache TTL buffer (refresh 5 minutes before expiry)
    CREDENTIAL_REFRESH_BUFFER_SECONDS = 300

    def __init__(self) -> None:
        """Initialize the connection manager."""
        settings = get_settings()

        client_kwargs: dict[str, Any] = {
            "service_name": "redshift",
            "region_name": settings.aws.region,
        }

        # Add credentials if explicitly provided
        if settings.aws.access_key_id and settings.aws.secret_access_key:
            client_kwargs["aws_access_key_id"] = settings.aws.access_key_id
            client_kwargs["aws_secret_access_key"] = (
                settings.aws.secret_access_key.get_secret_value()
            )

        # Add custom endpoint for LocalStack
        if settings.aws.endpoint_url:
            client_kwargs["endpoint_url"] = settings.aws.endpoint_url

        self._redshift_client = boto3.client(**client_kwargs)
        self._settings = settings
        self._credential_cache: dict[str, RedshiftCredentials] = {}

        logger.info(
            "Redshift connection manager initialized",
            cluster_id=settings.redshift.cluster_id,
            database=settings.redshift.database,
        )

    def get_credentials(
        self,
        db_user: str,
        db_groups: list[str] | None = None,
        auto_create: bool = False,
    ) -> RedshiftCredentials:
        """
        Get temporary credentials for Redshift connection.

        Args:
            db_user: Database user name
            db_groups: Optional list of database groups for the user
            auto_create: Whether to auto-create the user if it doesn't exist

        Returns:
            RedshiftCredentials with temporary password

        Raises:
            RedshiftConnectionError: If credential retrieval fails
        """
        # Create cache key
        cache_key = f"{db_user}:{','.join(db_groups or [])}"

        # Check cache
        if cache_key in self._credential_cache:
            cached = self._credential_cache[cache_key]
            if time.time() < cached.expiration - self.CREDENTIAL_REFRESH_BUFFER_SECONDS:
                logger.debug("Using cached Redshift credentials", db_user=db_user)
                return cached

        try:
            logger.info(
                "Obtaining Redshift credentials",
                db_user=db_user,
                db_groups=db_groups,
                cluster_id=self._settings.redshift.cluster_id,
            )

            request_params: dict[str, Any] = {
                "DbUser": db_user,
                "ClusterIdentifier": self._settings.redshift.cluster_id,
                "DbName": self._settings.redshift.database,
                "AutoCreate": auto_create,
            }

            if db_groups:
                request_params["DbGroups"] = db_groups

            response = self._redshift_client.get_cluster_credentials(**request_params)

            credentials = RedshiftCredentials(
                db_user=response["DbUser"],
                db_password=response["DbPassword"],
                expiration=response["Expiration"].timestamp(),
            )

            # Cache credentials
            self._credential_cache[cache_key] = credentials

            logger.info(
                "Obtained Redshift credentials",
                db_user=credentials.db_user,
                expires_in_seconds=int(credentials.expiration - time.time()),
            )

            return credentials

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(
                "Failed to get Redshift credentials",
                db_user=db_user,
                error_code=error_code,
                error_message=error_message,
            )
            raise RedshiftConnectionError(
                f"Failed to get credentials for user '{db_user}': {error_message}"
            ) from e

    @contextmanager
    def get_connection(
        self,
        db_user: str,
        db_groups: list[str] | None = None,
    ) -> Iterator[Connection]:
        """
        Get a Redshift database connection.

        Args:
            db_user: Database user name
            db_groups: Optional list of database groups

        Yields:
            psycopg Connection object

        Raises:
            RedshiftConnectionError: If connection fails
        """
        credentials = self.get_credentials(db_user, db_groups)

        try:
            logger.debug(
                "Creating Redshift connection",
                host=self._settings.redshift.host,
                port=self._settings.redshift.port,
                database=self._settings.redshift.database,
                user=credentials.db_user,
            )

            conn = psycopg.connect(
                host=self._settings.redshift.host,
                port=self._settings.redshift.port,
                dbname=self._settings.redshift.database,
                user=credentials.db_user,
                password=credentials.db_password,
                row_factory=dict_row,
                autocommit=True,
            )

            logger.info("Redshift connection established", user=credentials.db_user)
            yield conn

        except psycopg.Error as e:
            logger.error(
                "Failed to connect to Redshift",
                error=str(e),
                host=self._settings.redshift.host,
            )
            raise RedshiftConnectionError(f"Failed to connect to Redshift: {e}") from e

        finally:
            if "conn" in locals():
                conn.close()
                logger.debug("Redshift connection closed")

    def execute_query(
        self,
        query: str,
        db_user: str,
        db_groups: list[str] | None = None,
        max_rows: int | None = None,
    ) -> QueryResult:
        """
        Execute a SQL query on Redshift.

        Args:
            query: SQL query to execute
            db_user: Database user name
            db_groups: Optional list of database groups
            max_rows: Maximum number of rows to return (None = all)

        Returns:
            QueryResult with columns, rows, and metadata

        Raises:
            RedshiftQueryError: If query execution fails
        """
        start_time = time.time()

        try:
            with self.get_connection(db_user, db_groups) as conn:
                with conn.cursor() as cursor:
                    logger.info(
                        "Executing Redshift query",
                        user=db_user,
                        query_preview=query[:100] + "..." if len(query) > 100 else query,
                    )

                    cursor.execute(query)

                    # Get column names
                    columns = (
                        [desc[0] for desc in cursor.description]
                        if cursor.description
                        else []
                    )

                    # Fetch rows
                    if max_rows is not None:
                        rows = cursor.fetchmany(max_rows)
                        # Check if there are more rows
                        extra_row = cursor.fetchone()
                        truncated = extra_row is not None
                    else:
                        rows = cursor.fetchall()
                        truncated = False

                    execution_time_ms = (time.time() - start_time) * 1000

                    # Get actual row count if truncated
                    row_count = len(rows)
                    if truncated:
                        # We need to count remaining rows
                        remaining = len(cursor.fetchall())
                        row_count = len(rows) + 1 + remaining  # +1 for extra_row

                    result = QueryResult(
                        columns=columns,
                        rows=list(rows),
                        row_count=row_count,
                        execution_time_ms=execution_time_ms,
                        truncated=truncated,
                    )

                    logger.info(
                        "Query executed successfully",
                        row_count=result.row_count,
                        execution_time_ms=result.execution_time_ms,
                        truncated=result.truncated,
                    )

                    return result

        except psycopg.Error as e:
            logger.error("Query execution failed", error=str(e), query_preview=query[:100])
            raise RedshiftQueryError(f"Query execution failed: {e}") from e


# Singleton instance
_connection_manager: RedshiftConnectionManager | None = None


def get_redshift_connection_manager() -> RedshiftConnectionManager:
    """Get Redshift connection manager singleton."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = RedshiftConnectionManager()
    return _connection_manager
