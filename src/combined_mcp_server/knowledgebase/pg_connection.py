"""
PostgreSQL connection manager for pgvector.

Provides connection management and credential handling.
"""

from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator

import psycopg
from psycopg import AsyncConnection, Connection
from psycopg.rows import dict_row

from combined_mcp_server.config import get_settings
from combined_mcp_server.utils.logging import get_logger
from combined_mcp_server.utils.secrets import get_secrets_manager

logger = get_logger(__name__)


class PostgresConnectionError(Exception):
    """Custom exception for PostgreSQL connection errors."""

    pass


@dataclass
class PostgresCredentials:
    """PostgreSQL connection credentials."""

    host: str
    port: int
    database: str
    username: str
    password: str

    @property
    def connection_string(self) -> str:
        """Get connection string for psycopg."""
        return (
            f"host={self.host} port={self.port} dbname={self.database} "
            f"user={self.username} password={self.password}"
        )


class PostgresConnectionManager:
    """
    Manages PostgreSQL connections with pgvector support.

    Supports both direct credentials and AWS Secrets Manager.
    Uses direct connections (no pooling) for simplicity and reliability.
    """

    def __init__(self) -> None:
        """Initialize the connection manager."""
        self._settings = get_settings()
        self._credentials: PostgresCredentials | None = None

        logger.info("PostgreSQL connection manager initialized")

    def _get_credentials(self) -> PostgresCredentials:
        """
        Get PostgreSQL credentials.

        Returns credentials from Secrets Manager or direct configuration.
        """
        if self._credentials is not None:
            return self._credentials

        settings = self._settings.postgres

        if settings.use_secrets_manager:
            logger.info(
                "Retrieving PostgreSQL credentials from Secrets Manager",
                secret_name=settings.secret_name,
            )
            secrets_manager = get_secrets_manager()
            secret = secrets_manager.get_secret(settings.secret_name)  # type: ignore

            self._credentials = PostgresCredentials(
                host=secret.get("host", settings.host or "localhost"),
                port=int(secret.get("port", settings.port)),
                database=secret.get("database", settings.database or "vectordb"),
                username=secret.get("username", settings.user or "postgres"),
                password=secret.get("password", ""),
            )
        else:
            logger.info("Using direct PostgreSQL credentials")
            if not all([settings.host, settings.database, settings.user]):
                raise PostgresConnectionError(
                    "PostgreSQL credentials incomplete. "
                    "Set POSTGRES_SECRET_NAME or provide direct credentials."
                )

            self._credentials = PostgresCredentials(
                host=settings.host,  # type: ignore
                port=settings.port,
                database=settings.database,  # type: ignore
                username=settings.user,  # type: ignore
                password=settings.password.get_secret_value() if settings.password else "",
            )

        return self._credentials

    @contextmanager
    def get_connection(self) -> Iterator[Connection]:
        """
        Get a synchronous database connection.

        Yields:
            psycopg Connection object
        """
        credentials = self._get_credentials()
        with psycopg.connect(
            credentials.connection_string,
            row_factory=dict_row,
        ) as conn:
            yield conn

    @asynccontextmanager
    async def get_async_connection(self) -> AsyncIterator[AsyncConnection]:
        """
        Get an asynchronous database connection.

        Yields:
            psycopg AsyncConnection object
        """
        credentials = self._get_credentials()
        async with await AsyncConnection.connect(
            credentials.connection_string,
            row_factory=dict_row,
        ) as conn:
            yield conn

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        """
        Execute a query and return results.

        Args:
            query: SQL query
            params: Query parameters

        Returns:
            List of result dictionaries
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                if cursor.description:
                    return list(cursor.fetchall())
                return []

    async def execute_async(
        self,
        query: str,
        params: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute a query asynchronously and return results.

        Args:
            query: SQL query
            params: Query parameters

        Returns:
            List of result dictionaries
        """
        async with self.get_async_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query, params)
                if cursor.description:
                    return list(await cursor.fetchall())
                return []

    async def close(self) -> None:
        """Close connections (no-op for direct connections)."""
        logger.info("Connection manager closed")


# Singleton instance
_connection_manager: PostgresConnectionManager | None = None


def get_postgres_connection_manager() -> PostgresConnectionManager:
    """Get PostgreSQL connection manager singleton."""
    global _connection_manager
    if _connection_manager is None:
        _connection_manager = PostgresConnectionManager()
    return _connection_manager
