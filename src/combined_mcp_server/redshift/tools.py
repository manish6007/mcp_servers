"""
Redshift MCP tools.

Provides MCP tool implementations for Redshift operations.
"""

import time
from typing import Any

from combined_mcp_server.redshift.connection import (
    QueryResult,
    RedshiftConnectionError,
    RedshiftQueryError,
    get_redshift_connection_manager,
)
from combined_mcp_server.redshift.s3_storage import get_result_storage
from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)

# Threshold for storing results in S3
LARGE_RESULT_THRESHOLD = 100
# Number of sample rows to return for large results
SAMPLE_ROWS_COUNT = 20


async def run_query(
    sql: str,
    db_user: str,
    db_group: str | None = None,
) -> dict[str, Any]:
    """
    Execute a SQL query on Redshift.

    For queries returning more than 100 rows, the full result set is stored
    in S3 and only 20 sample rows are returned directly.

    Args:
        sql: The SQL query to execute
        db_user: Database user for authentication via get_cluster_credentials
        db_group: Optional database group for permissions

    Returns:
        Dictionary containing:
        - row_count: Total number of rows in result
        - columns: List of column names
        - rows: Result rows (all if <= 100, sample of 20 if > 100)
        - execution_time_ms: Query execution time in milliseconds
        - is_sample: Whether the returned rows are a sample
        - s3_path: S3 URI of full results (only if > 100 rows)
        - s3_download_url: Presigned URL for downloading full results
    """
    logger.info(
        "run_query tool invoked",
        db_user=db_user,
        db_group=db_group,
        query_preview=sql[:100] + "..." if len(sql) > 100 else sql,
    )

    start_time = time.time()

    try:
        connection_manager = get_redshift_connection_manager()
        db_groups = [db_group] if db_group else None

        # Execute query and fetch all rows
        result = connection_manager.execute_query(
            query=sql,
            db_user=db_user,
            db_groups=db_groups,
        )

        response: dict[str, Any] = {
            "success": True,
            "row_count": result.row_count,
            "columns": result.columns,
            "execution_time_ms": result.execution_time_ms,
            "is_sample": False,
        }

        # Check if we need to store in S3
        if result.row_count > LARGE_RESULT_THRESHOLD:
            logger.info(
                "Large result set detected, storing in S3",
                row_count=result.row_count,
                threshold=LARGE_RESULT_THRESHOLD,
            )

            # Store full results in S3
            result_storage = get_result_storage()
            stored = result_storage.store_results(
                query=sql,
                columns=result.columns,
                rows=result.rows,
            )

            # Return only sample rows
            response["rows"] = result.rows[:SAMPLE_ROWS_COUNT]
            response["is_sample"] = True
            response["sample_count"] = len(response["rows"])
            response["s3_path"] = stored.s3_uri
            response["s3_download_url"] = stored.presigned_url
            response["message"] = (
                f"Result contains {result.row_count} rows. "
                f"Returning {SAMPLE_ROWS_COUNT} sample rows. "
                f"Full results available at S3 path."
            )
        else:
            response["rows"] = result.rows

        total_time_ms = (time.time() - start_time) * 1000
        response["total_time_ms"] = total_time_ms

        logger.info(
            "run_query completed",
            row_count=result.row_count,
            is_sample=response["is_sample"],
            total_time_ms=total_time_ms,
        )

        return response

    except (RedshiftConnectionError, RedshiftQueryError) as e:
        logger.error("run_query failed", error=str(e))
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }


async def list_schemas(
    db_user: str,
    db_group: str | None = None,
) -> dict[str, Any]:
    """
    List all schemas in the Redshift database.

    Args:
        db_user: Database user for authentication
        db_group: Optional database group for permissions

    Returns:
        Dictionary containing list of schema names
    """
    logger.info("list_schemas tool invoked", db_user=db_user)

    query = """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        ORDER BY schema_name
    """

    try:
        connection_manager = get_redshift_connection_manager()
        db_groups = [db_group] if db_group else None

        result = connection_manager.execute_query(
            query=query,
            db_user=db_user,
            db_groups=db_groups,
        )

        schemas = [row["schema_name"] for row in result.rows]

        logger.info("list_schemas completed", schema_count=len(schemas))

        return {
            "success": True,
            "schemas": schemas,
            "count": len(schemas),
        }

    except (RedshiftConnectionError, RedshiftQueryError) as e:
        logger.error("list_schemas failed", error=str(e))
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }


async def list_tables(
    schema: str,
    db_user: str,
    db_group: str | None = None,
) -> dict[str, Any]:
    """
    List all tables in a Redshift schema.

    Args:
        schema: Schema name to list tables from
        db_user: Database user for authentication
        db_group: Optional database group for permissions

    Returns:
        Dictionary containing list of table names with metadata
    """
    logger.info("list_tables tool invoked", schema=schema, db_user=db_user)

    query = f"""
        SELECT 
            t.table_name,
            t.table_type,
            COALESCE(c.row_count, 0) as estimated_row_count
        FROM information_schema.tables t
        LEFT JOIN (
            SELECT schemaname, tablename, 
                   COALESCE(reltuples::bigint, 0) as row_count
            FROM pg_class pc
            JOIN pg_namespace pn ON pc.relnamespace = pn.oid
            WHERE pn.nspname = '{schema}'
        ) c ON t.table_name = c.tablename
        WHERE t.table_schema = '{schema}'
        ORDER BY t.table_name
    """

    try:
        connection_manager = get_redshift_connection_manager()
        db_groups = [db_group] if db_group else None

        result = connection_manager.execute_query(
            query=query,
            db_user=db_user,
            db_groups=db_groups,
        )

        tables = [
            {
                "name": row["table_name"],
                "type": row["table_type"],
                "estimated_rows": row["estimated_row_count"],
            }
            for row in result.rows
        ]

        logger.info("list_tables completed", schema=schema, table_count=len(tables))

        return {
            "success": True,
            "schema": schema,
            "tables": tables,
            "count": len(tables),
        }

    except (RedshiftConnectionError, RedshiftQueryError) as e:
        logger.error("list_tables failed", error=str(e))
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }


async def describe_table(
    schema: str,
    table: str,
    db_user: str,
    db_group: str | None = None,
) -> dict[str, Any]:
    """
    Get detailed information about a Redshift table.

    Args:
        schema: Schema name
        table: Table name
        db_user: Database user for authentication
        db_group: Optional database group for permissions

    Returns:
        Dictionary containing table structure and metadata
    """
    logger.info(
        "describe_table tool invoked",
        schema=schema,
        table=table,
        db_user=db_user,
    )

    columns_query = f"""
        SELECT 
            column_name,
            data_type,
            character_maximum_length,
            numeric_precision,
            numeric_scale,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = '{schema}'
          AND table_name = '{table}'
        ORDER BY ordinal_position
    """

    try:
        connection_manager = get_redshift_connection_manager()
        db_groups = [db_group] if db_group else None

        result = connection_manager.execute_query(
            query=columns_query,
            db_user=db_user,
            db_groups=db_groups,
        )

        columns = [
            {
                "name": row["column_name"],
                "data_type": row["data_type"],
                "max_length": row["character_maximum_length"],
                "precision": row["numeric_precision"],
                "scale": row["numeric_scale"],
                "nullable": row["is_nullable"] == "YES",
                "default": row["column_default"],
            }
            for row in result.rows
        ]

        logger.info(
            "describe_table completed",
            schema=schema,
            table=table,
            column_count=len(columns),
        )

        return {
            "success": True,
            "schema": schema,
            "table": table,
            "columns": columns,
            "column_count": len(columns),
        }

    except (RedshiftConnectionError, RedshiftQueryError) as e:
        logger.error("describe_table failed", error=str(e))
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }
