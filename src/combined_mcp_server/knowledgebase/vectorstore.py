"""
Vector store implementation with hybrid search.

Manages document storage, embedding, and search in PostgreSQL with pgvector.
"""

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from combined_mcp_server.config import get_settings
from combined_mcp_server.knowledgebase.cache import get_query_cache
from combined_mcp_server.knowledgebase.embeddings import get_embeddings_client
from combined_mcp_server.knowledgebase.pg_connection import get_postgres_connection_manager
from combined_mcp_server.knowledgebase.reranker import get_reranker
from combined_mcp_server.knowledgebase.schema_parser import (
    is_schema_markdown,
    parse_schema_markdown,
    build_embedding_text,
    build_schema_toon,
)
from combined_mcp_server.utils.logging import get_logger
from combined_mcp_server.utils.s3 import get_s3_client

logger = get_logger(__name__)


class VectorStoreError(Exception):
    """Custom exception for vector store operations."""

    pass


@dataclass
class Document:
    """A document for the vector store."""

    content: str
    metadata: dict[str, Any]
    source_path: str
    chunk_index: int = 0
    schema_toon: str | None = None  # Pre-encoded TOON for schema files


@dataclass
class BuildStatus:
    """Vector store build status."""

    status: str  # 'pending', 'building', 'ready', 'failed'
    document_count: int
    last_build_started_at: datetime | None
    last_build_completed_at: datetime | None
    last_error: str | None


class VectorStore:
    """
    Vector store with hybrid search capabilities.

    Supports:
    - Semantic search using pgvector (HNSW index)
    - Keyword search using PostgreSQL full-text search
    - Hybrid search combining both with RRF reranking
    - Query caching for repeated queries
    """

    # Chunk size for splitting large documents
    CHUNK_SIZE = 1000
    CHUNK_OVERLAP = 100

    def __init__(self) -> None:
        """Initialize vector store."""
        self._settings = get_settings()
        self._db = get_postgres_connection_manager()
        self._embeddings = get_embeddings_client()
        self._cache = get_query_cache()
        self._reranker = get_reranker()
        self._is_ready = False

        logger.info("Vector store initialized")

    def _chunk_text(self, text: str) -> list[str]:
        """
        Split text into overlapping chunks.

        Args:
            text: Text to split

        Returns:
            List of text chunks
        """
        if len(text) <= self.CHUNK_SIZE:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + self.CHUNK_SIZE

            # Try to break at sentence boundary
            if end < len(text):
                # Look for sentence end in the last 20% of chunk
                search_start = int(end * 0.8)
                sentence_end = max(
                    text.rfind(". ", search_start, end),
                    text.rfind("! ", search_start, end),
                    text.rfind("? ", search_start, end),
                    text.rfind("\n", search_start, end),
                )
                if sentence_end > search_start:
                    end = sentence_end + 1

            chunks.append(text[start:end].strip())
            start = end - self.CHUNK_OVERLAP

        return [c for c in chunks if c]

    async def build_from_s3(self) -> BuildStatus:
        """
        Build vector store from S3 markdown files.

        Downloads markdown files from configured S3 location,
        chunks them, generates embeddings, and stores in PostgreSQL.

        Returns:
            BuildStatus with results
        """
        settings = self._settings
        bucket = settings.knowledgebase.s3_bucket
        prefix = settings.knowledgebase.s3_prefix

        logger.info(
            "Building vector store from S3",
            bucket=bucket,
            prefix=prefix,
        )

        # Update status to building
        await self._update_build_status("building")
        start_time = time.time()

        try:
            s3_client = get_s3_client()

            # List markdown files
            files = list(s3_client.list_files(bucket, prefix, suffix=".md"))
            logger.info("Found markdown files", count=len(files))

            if not files:
                logger.warning("No markdown files found in S3")
                await self._update_build_status("ready", document_count=0)
                return await self.get_build_status()

            # Clear existing documents
            await self._clear_documents()

            # Process each file
            total_chunks = 0
            for file_info in files:
                key = file_info["key"]
                logger.debug("Processing file", key=key)

                # Download file
                content = s3_client.download_text(bucket, key)

                # Check if this is a schema markdown file
                if is_schema_markdown(content):
                    # Parse as schema for TOON encoding
                    schema = parse_schema_markdown(content)
                    if schema:
                        # Use embedding-optimized text for vector
                        embedding_text = build_embedding_text(schema)
                        schema_toon = build_schema_toon(schema)
                        
                        doc = Document(
                            content=embedding_text,
                            metadata={
                                "title": schema.table_name,
                                "source_file": key,
                                "chunk_index": 0,
                                "total_chunks": 1,
                                "is_schema": True,
                            },
                            source_path=key,
                            chunk_index=0,
                            schema_toon=schema_toon,
                        )
                        await self._insert_document(doc)
                        total_chunks += 1
                        continue  # Skip normal processing

                # Normal document processing (non-schema)
                title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
                title = title_match.group(1) if title_match else key.split("/")[-1]

                # Chunk content
                chunks = self._chunk_text(content)

                # Process each chunk
                for idx, chunk in enumerate(chunks):
                    doc = Document(
                        content=chunk,
                        metadata={
                            "title": title,
                            "source_file": key,
                            "chunk_index": idx,
                            "total_chunks": len(chunks),
                        },
                        source_path=key,
                        chunk_index=idx,
                    )
                    await self._insert_document(doc)
                    total_chunks += 1

            # Update status to ready
            build_time = time.time() - start_time
            await self._update_build_status("ready", document_count=total_chunks)

            # Clear cache since data changed
            self._cache.clear()
            self._is_ready = True

            logger.info(
                "Vector store build completed",
                file_count=len(files),
                document_count=total_chunks,
                build_time_seconds=build_time,
            )

            return await self.get_build_status()

        except Exception as e:
            logger.error("Vector store build failed", error=str(e))
            await self._update_build_status("failed", error=str(e))
            raise VectorStoreError(f"Failed to build vector store: {e}") from e

    async def _insert_document(self, doc: Document) -> None:
        """Insert a document with embedding."""
        # Generate embedding
        embedding = await self._embeddings.embed_text_async(doc.content)

        # Insert into database with schema_toon if available
        query = """
            INSERT INTO knowledgebase.documents 
            (content, metadata, schema_toon, embedding, source_path, chunk_index)
            VALUES (%s, %s, %s, %s, %s, %s)
        """

        import json

        await self._db.execute_async(
            query,
            (
                doc.content,
                json.dumps(doc.metadata),
                doc.schema_toon,
                embedding,
                doc.source_path,
                doc.chunk_index,
            ),
        )

    async def _clear_documents(self) -> None:
        """Clear all documents from the vector store."""
        await self._db.execute_async("TRUNCATE TABLE knowledgebase.documents")
        logger.info("Cleared existing documents")

    async def _update_build_status(
        self,
        status: str,
        document_count: int | None = None,
        error: str | None = None,
    ) -> None:
        """Update build status in database."""
        now = datetime.now(timezone.utc)

        if status == "building":
            query = """
                UPDATE knowledgebase.build_status
                SET status = %s, last_build_started_at = %s, last_error = NULL
                WHERE id = 1
            """
            await self._db.execute_async(query, (status, now))
        elif status == "ready":
            query = """
                UPDATE knowledgebase.build_status
                SET status = %s, document_count = %s, 
                    last_build_completed_at = %s, last_error = NULL
                WHERE id = 1
            """
            await self._db.execute_async(query, (status, document_count, now))
        else:
            query = """
                UPDATE knowledgebase.build_status
                SET status = %s, last_error = %s
                WHERE id = 1
            """
            await self._db.execute_async(query, (status, error))

    async def get_build_status(self) -> BuildStatus:
        """Get current build status."""
        query = """
            SELECT status, document_count, last_build_started_at, 
                   last_build_completed_at, last_error
            FROM knowledgebase.build_status
            WHERE id = 1
        """
        result = await self._db.execute_async(query)
        if result:
            row = result[0]
            return BuildStatus(
                status=row["status"],
                document_count=row["document_count"],
                last_build_started_at=row["last_build_started_at"],
                last_build_completed_at=row["last_build_completed_at"],
                last_error=row["last_error"],
            )
        return BuildStatus(
            status="pending",
            document_count=0,
            last_build_started_at=None,
            last_build_completed_at=None,
            last_error=None,
        )

    async def search(
        self,
        query: str,
        top_k: int = 10,
        search_type: str = "hybrid",
    ) -> list[dict[str, Any]]:
        """
        Search the vector store.

        Args:
            query: Search query
            top_k: Number of results to return
            search_type: "semantic", "keyword", or "hybrid"

        Returns:
            List of search results
        """
        # Check cache first
        cached = self._cache.get(query, top_k, search_type)
        if cached is not None:
            return cached

        start_time = time.time()

        if search_type == "semantic":
            results = await self._semantic_search(query, top_k)
        elif search_type == "keyword":
            results = await self._keyword_search(query, top_k)
        else:  # hybrid
            results = await self._hybrid_search(query, top_k)

        search_time_ms = (time.time() - start_time) * 1000

        # Add search metadata
        for result in results:
            result["search_time_ms"] = search_time_ms
            result["search_type"] = search_type

        # Cache results
        self._cache.put(query, top_k, search_type, results)

        logger.info(
            "Search completed",
            query_preview=query[:50],
            search_type=search_type,
            result_count=len(results),
            search_time_ms=search_time_ms,
        )

        return results

    async def _semantic_search(
        self,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Perform semantic (vector) search."""
        # Generate query embedding
        query_embedding = await self._embeddings.embed_text_async(query)

        sql = """
            SELECT 
                id, content, metadata, schema_toon,
                1 - (embedding <=> %s::vector) as score
            FROM knowledgebase.documents
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """

        results = await self._db.execute_async(sql, (query_embedding, query_embedding, top_k))

        return [
            {
                "id": row["id"],
                "content": row["content"],
                "metadata": row["metadata"],
                "schema_toon": row.get("schema_toon"),
                "score": float(row["score"]),
            }
            for row in results
        ]

    async def _keyword_search(
        self,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Perform keyword (full-text) search."""
        sql = """
            SELECT 
                id, content, metadata, schema_toon,
                ts_rank_cd(fts, plainto_tsquery('english', %s)) as score
            FROM knowledgebase.documents
            WHERE fts @@ plainto_tsquery('english', %s)
            ORDER BY score DESC
            LIMIT %s
        """

        results = await self._db.execute_async(sql, (query, query, top_k))

        return [
            {
                "id": row["id"],
                "content": row["content"],
                "metadata": row["metadata"],
                "schema_toon": row.get("schema_toon"),
                "score": float(row["score"]),
            }
            for row in results
        ]

    async def _hybrid_search(
        self,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Perform hybrid search with RRF reranking."""
        # Get more results from each method for reranking
        fetch_count = min(top_k * 3, 50)

        # Run both searches
        semantic_results = await self._semantic_search(query, fetch_count)
        keyword_results = await self._keyword_search(query, fetch_count)

        # Fuse with RRF
        ranked = self._reranker.fuse(semantic_results, keyword_results, top_k)

        return self._reranker.to_dict_list(ranked)

    @property
    def is_ready(self) -> bool:
        """Check if vector store is ready for queries."""
        return self._is_ready


# Singleton instance
_vector_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """Get vector store singleton."""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store
