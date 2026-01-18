"""
Combined MCP Server - Main Entry Point.

Provides a unified MCP server with Redshift and Knowledgebase capabilities.
Uses FastMCP for better tooling compatibility.
"""

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any, Literal

# Fix for Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from mcp.server.sse import SseServerTransport

from combined_mcp_server.config import get_settings
from combined_mcp_server.knowledgebase import tools as kb_tools
from combined_mcp_server.knowledgebase.vectorstore import get_vector_store
from combined_mcp_server.knowledgebase.pg_connection import get_postgres_connection_manager
from combined_mcp_server.redshift import tools as redshift_tools
from combined_mcp_server.utils.logging import configure_logging, get_logger


# Initialize logging
configure_logging()
logger = get_logger(__name__)

# Create FastMCP server instance - global for mcp dev compatibility
mcp = FastMCP("combined-mcp-server")


# =============================================================================
# Redshift Tools
# =============================================================================

@mcp.tool()
async def run_query(
    sql: str,
    db_user: str,
    db_group: str | None = None,
) -> dict[str, Any]:
    """
    Execute a SQL query on Redshift.
    
    For queries returning more than 100 rows, the full result set is stored 
    in S3 and only 20 sample rows are returned.
    
    Args:
        sql: The SQL query to execute
        db_user: Database user for authentication via get_cluster_credentials
        db_group: Optional database group for permissions
    """
    return await redshift_tools.run_query(sql=sql, db_user=db_user, db_group=db_group)


@mcp.tool()
async def list_schemas(
    db_user: str,
    db_group: str | None = None,
) -> dict[str, Any]:
    """
    List all schemas in the Redshift database.
    
    Args:
        db_user: Database user for authentication
        db_group: Optional database group for permissions
    """
    return await redshift_tools.list_schemas(db_user=db_user, db_group=db_group)


@mcp.tool()
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
    """
    return await redshift_tools.list_tables(schema=schema, db_user=db_user, db_group=db_group)


@mcp.tool()
async def describe_table(
    schema: str,
    table: str,
    db_user: str,
    db_group: str | None = None,
) -> dict[str, Any]:
    """
    Get detailed information about a Redshift table including columns and data types.
    
    Args:
        schema: Schema name
        table: Table name
        db_user: Database user for authentication
        db_group: Optional database group for permissions
    """
    return await redshift_tools.describe_table(
        schema=schema, table=table, db_user=db_user, db_group=db_group
    )


# =============================================================================
# Knowledgebase Tools
# =============================================================================

@mcp.tool()
async def build_vectorstore() -> dict[str, Any]:
    """
    Build or rebuild the knowledge base vector store from S3 markdown files.
    
    Downloads all markdown files from the configured S3 location, processes them 
    into chunks, generates embeddings using AWS Bedrock Titan, and stores in PostgreSQL.
    """
    return await kb_tools.build_vectorstore()


@mcp.tool()
async def query_vectorstore(
    query: str,
    top_k: int = 10,
    search_type: Literal["semantic", "keyword", "hybrid"] = "hybrid",
) -> dict[str, Any]:
    """
    Search the knowledge base vector store.
    
    Supports semantic search (vector similarity), keyword search (full-text), 
    or hybrid search combining both with RRF reranking. Results are cached for performance.
    
    Args:
        query: The search query text
        top_k: Maximum number of results to return (default: 10)
        search_type: Type of search - semantic, keyword, or hybrid (default)
    """
    return await kb_tools.query_vectorstore(
        query=query, top_k=top_k, search_type=search_type
    )


@mcp.tool()
async def get_vectorstore_status() -> dict[str, Any]:
    """
    Get the current status of the knowledge base vector store.
    
    Returns build status, document count, and cache statistics.
    """
    return await kb_tools.get_vectorstore_status()


@mcp.tool()
async def query_schemas(
    query: str,
    top_k: int = 3,
) -> dict[str, Any]:
    """
    Search for database schemas relevant to a Text2SQL query.
    
    Returns TOON-encoded schema information optimized for LLM context.
    Use this tool to get table and column definitions before generating SQL.
    
    Args:
        query: The natural language query (e.g., "total sales by region")
        top_k: Maximum number of schemas to return (default: 3)
    """
    return await kb_tools.query_schemas(query=query, top_k=top_k)


# =============================================================================
# Startup and Main
# =============================================================================

async def initialize_vectorstore(health_server) -> None:
    """Initialize vector store on startup."""
    logger.info("Initializing vector store on startup...")

    try:
        # Test PostgreSQL connection
        pg_manager = get_postgres_connection_manager()
        result = pg_manager.execute("SELECT 1")
        health_server.set_component_status("postgres", ready=True)
        logger.info("PostgreSQL connection verified")

        # Build vector store
        vector_store = get_vector_store()
        status = await vector_store.build_from_s3()

        health_server.set_component_status(
            "vectorstore",
            ready=status.status == "ready",
            document_count=status.document_count,
            error=status.last_error,
        )

        if status.status == "ready":
            logger.info(
                "Vector store initialized successfully",
                document_count=status.document_count,
            )
        else:
            logger.warning(
                "Vector store initialization completed with status",
                status=status.status,
                error=status.last_error,
            )

    except Exception as e:
        logger.error("Failed to initialize vector store", error=str(e))
        health_server.set_component_status("vectorstore", ready=False, error=str(e))


# =============================================================================
# Health Probes (FastMCP Native)
# =============================================================================

@mcp.custom_route("/health", methods=["GET"])
async def health_check_handler(request: Request) -> Response:
    """Liveness probe for ECS."""
    return JSONResponse({"status": "alive", "timestamp": datetime.now(timezone.utc).isoformat()})


@mcp.custom_route("/ready", methods=["GET"])
async def readiness_check_handler(request: Request) -> Response:
    """Readiness probe for ECS."""
    from combined_mcp_server.knowledgebase.pg_connection import get_postgres_connection_manager
    from combined_mcp_server.knowledgebase.vectorstore import get_vector_store
    
    try:
        # Check DB
        pg = get_postgres_connection_manager()
        await pg.execute_async("SELECT 1")
        
        # Check Vector Store
        vs = get_vector_store()
        status = await vs.get_build_status()
        
        is_ready = status.status == "ready"
        return JSONResponse({
            "ready": is_ready,
            "components": {
                "postgres": True,
                "vectorstore": is_ready
            }
        }, status_code=200 if is_ready else 503)
    except Exception as e:
        return JSONResponse({"ready": False, "error": str(e)}, status_code=503)


# =============================================================================
# Startup and Main
# =============================================================================

async def initialize_vectorstore() -> None:
    """Background task to initialize vector store on startup."""
    logger.info("Initializing vector store on startup...")
    try:
        from combined_mcp_server.knowledgebase.vectorstore import get_vector_store
        vector_store = get_vector_store()
        await vector_store.build_from_s3()
        logger.info("Vector store initialization complete")
    except Exception as e:
        logger.error("Failed to initialize vector store", error=str(e))


def main() -> None:
    """Main entry point (stdio mode)."""
    # Fire and forget KB init (FastMCP will handle the loop)
    try:
        # Standard stdio run
        mcp.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error("Server failed", error=str(e))
        sys.exit(1)


def main_http() -> None:
    """HTTP mode entry point for ECS."""
    import os
    host = os.getenv("MCP_HTTP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_HTTP_PORT", "8080"))
    
    logger.info(f"Starting Combined MCP Server (HTTP mode on {host}:{port})...")
    
    # Set host/port via settings
    mcp.settings.host = host
    mcp.settings.port = port
    
    # Use the correct API: mcp.run(transport="streamable-http")
    try:
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error("Server failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    import os
    if os.getenv("MCP_TRANSPORT", "stdio") == "http":
        main_http()
    else:
        main()

