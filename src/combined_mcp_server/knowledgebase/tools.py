"""
Knowledgebase MCP tools.

Provides MCP tool implementations for knowledgebase operations.
"""

import time
from typing import Any, Literal

from combined_mcp_server.knowledgebase.cache import get_query_cache
from combined_mcp_server.knowledgebase.vectorstore import (
    VectorStoreError,
    get_vector_store,
)
from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)


async def build_vectorstore() -> dict[str, Any]:
    """
    Build or rebuild the vector store from S3 markdown files.

    Downloads all markdown files from the configured S3 location,
    processes them into chunks, generates embeddings, and stores
    in PostgreSQL with pgvector.

    Returns:
        Dictionary containing:
        - success: Whether the build succeeded
        - status: Current build status
        - document_count: Number of documents indexed
        - build_time_seconds: Time taken to build
    """
    logger.info("build_vectorstore tool invoked")

    start_time = time.time()

    try:
        vector_store = get_vector_store()
        status = await vector_store.build_from_s3()

        build_time = time.time() - start_time

        return {
            "success": True,
            "message": f"SUCCESS: The knowledge base has been built successfully. Indexed {status.document_count} chunks in {build_time:.2f} seconds.",
            "status": status.status,
            "document_count": status.document_count,
            "build_time_seconds": build_time,
            "last_build_completed_at": (
                status.last_build_completed_at.isoformat()
                if status.last_build_completed_at
                else None
            ),
        }

    except VectorStoreError as e:
        logger.error("build_vectorstore failed", error=str(e))
        return {
            "success": False,
            "message": f"CRITICAL FAILURE: The vector store build failed with error: {str(e)}",
            "error_type": "VectorStoreError",
            "suggestion": "Check AWS credentials and S3 bucket permissions.",
        }
    except Exception as e:
        logger.error("build_vectorstore failed with unexpected error", error=str(e))
        return {
            "success": False,
            "message": f"UNEXPECTED ERROR: An internal error occurred: {str(e)}",
            "error_type": type(e).__name__,
        }


async def query_vectorstore(
    query: str,
    top_k: int = 10,
    search_type: Literal["semantic", "keyword", "hybrid"] = "hybrid",
) -> dict[str, Any]:
    """
    Search the knowledge base vector store.

    Supports three search modes:
    - semantic: Vector similarity search using embeddings
    - keyword: Full-text search using PostgreSQL FTS
    - hybrid: Combines both using Reciprocal Rank Fusion (RRF)

    Results are cached to serve repeated queries efficiently.

    Args:
        query: The search query text
        top_k: Maximum number of results to return (default: 10)
        search_type: Type of search - "semantic", "keyword", or "hybrid" (default)

    Returns:
        Dictionary containing:
        - success: Whether the search succeeded
        - results: List of matching documents with content, metadata, and scores
        - result_count: Number of results returned
        - search_type: Type of search performed
        - query_time_ms: Time taken for the query
        - cached: Whether the result was served from cache
    """
    logger.info(
        "query_vectorstore tool invoked",
        query_preview=query[:50] if query else "",
        top_k=top_k,
        search_type=search_type,
    )

    start_time = time.time()

    try:
        vector_store = get_vector_store()

        # Check if vector store is ready
        status = await vector_store.get_build_status()
        if status.status != "ready":
            return {
                "success": False,
                "error": f"Vector store is not ready. Current status: {status.status}",
                "status": status.status,
                "suggestion": "Run build_vectorstore to initialize the vector store.",
            }

        # Check cache
        cache = get_query_cache()
        cached_results = cache.get(query, top_k, search_type)
        is_cached = cached_results is not None

        # Perform search
        results = await vector_store.search(
            query=query,
            top_k=top_k,
            search_type=search_type,
        )

        query_time_ms = (time.time() - start_time) * 1000

        return {
            "success": True,
            "results": results,
            "result_count": len(results),
            "search_type": search_type,
            "query_time_ms": query_time_ms,
            "cached": is_cached,
            "top_k": top_k,
        }

    except VectorStoreError as e:
        logger.error("query_vectorstore failed", error=str(e))
        return {
            "success": False,
            "error": str(e),
            "error_type": "VectorStoreError",
        }
    except Exception as e:
        logger.error("query_vectorstore failed with unexpected error", error=str(e))
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }


async def get_vectorstore_status() -> dict[str, Any]:
    """
    Get the current status of the knowledge base vector store.

    Returns information about the vector store including:
    - Build status (pending, building, ready, failed)
    - Document count
    - Last build times
    - Cache statistics

    Returns:
        Dictionary containing vector store status and statistics
    """
    logger.info("get_vectorstore_status tool invoked")

    try:
        vector_store = get_vector_store()
        status = await vector_store.get_build_status()

        cache = get_query_cache()
        cache_stats = cache.get_stats()

        return {
            "success": True,
            "status": status.status,
            "document_count": status.document_count,
            "last_build_started_at": (
                status.last_build_started_at.isoformat()
                if status.last_build_started_at
                else None
            ),
            "last_build_completed_at": (
                status.last_build_completed_at.isoformat()
                if status.last_build_completed_at
                else None
            ),
            "last_error": status.last_error,
            "cache_stats": cache_stats,
            "is_ready": vector_store.is_ready,
        }

    except Exception as e:
        logger.error("get_vectorstore_status failed", error=str(e))
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }
