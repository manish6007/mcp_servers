"""
AWS Bedrock Titan embeddings client.

Generates embeddings using Amazon Titan Text Embeddings V2.
"""

import asyncio
import time
from typing import Any

import boto3
import json
from botocore.exceptions import ClientError

from combined_mcp_server.config import get_settings
from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)


class EmbeddingError(Exception):
    """Custom exception for embedding operations."""

    pass


class BedrockEmbeddings:
    """
    AWS Bedrock Titan embeddings client.

    Uses Amazon Titan Text Embeddings V2 model (1024 dimensions).
    Includes rate limiting and batch processing support.
    """

    # Rate limiting settings
    REQUESTS_PER_SECOND = 10
    BATCH_SIZE = 25

    def __init__(self) -> None:
        """Initialize Bedrock embeddings client."""
        settings = get_settings()

        client_kwargs: dict[str, Any] = {
            "service_name": "bedrock-runtime",
            "region_name": settings.bedrock.region,
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

        self._client = boto3.client(**client_kwargs)
        self._model_id = settings.bedrock.embedding_model
        self._dimension = settings.vectorstore.embedding_dimension
        self._last_request_time = 0.0

        logger.info(
            "Bedrock embeddings client initialized",
            model_id=self._model_id,
            dimension=self._dimension,
        )

    def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        min_interval = 1.0 / self.REQUESTS_PER_SECOND
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

    async def _rate_limit_async(self) -> None:
        """Apply rate limiting between requests (async)."""
        min_interval = 1.0 / self.REQUESTS_PER_SECOND
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

    def embed_text(self, text: str) -> list[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        self._rate_limit()

        try:
            # Prepare request body for Titan Embeddings V2
            body = json.dumps({
                "inputText": text,
                "dimensions": self._dimension,
                "normalize": True,
            })

            response = self._client.invoke_model(
                modelId=self._model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )

            response_body = json.loads(response["body"].read())
            embedding = response_body["embedding"]

            logger.debug(
                "Generated embedding",
                text_length=len(text),
                embedding_dimension=len(embedding),
            )

            return embedding

        except ClientError as e:
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error("Failed to generate embedding", error=error_message)
            raise EmbeddingError(f"Failed to generate embedding: {error_message}") from e

    async def embed_text_async(self, text: str) -> list[float]:
        """
        Generate embedding for a single text (async).

        Args:
            text: Text to embed

        Returns:
            Embedding vector as list of floats
        """
        await self._rate_limit_async()

        # Run sync client in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._embed_text_sync(text),
        )

    def _embed_text_sync(self, text: str) -> list[float]:
        """Synchronous embedding without rate limiting (for internal use)."""
        try:
            body = json.dumps({
                "inputText": text,
                "dimensions": self._dimension,
                "normalize": True,
            })

            response = self._client.invoke_model(
                modelId=self._model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )

            response_body = json.loads(response["body"].read())
            return response_body["embedding"]

        except ClientError as e:
            error_message = e.response.get("Error", {}).get("Message", str(e))
            raise EmbeddingError(f"Failed to generate embedding: {error_message}") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a batch of texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        logger.info(
            "Generating batch embeddings",
            batch_size=len(texts),
        )

        embeddings = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]
            for text in batch:
                embedding = self.embed_text(text)
                embeddings.append(embedding)

            logger.debug(
                "Batch progress",
                processed=min(i + self.BATCH_SIZE, len(texts)),
                total=len(texts),
            )

        logger.info(
            "Batch embeddings completed",
            total_embeddings=len(embeddings),
        )

        return embeddings

    async def embed_batch_async(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a batch of texts (async).

        Processes in smaller batches to respect rate limits.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        logger.info(
            "Generating batch embeddings (async)",
            batch_size=len(texts),
        )

        embeddings = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]

            # Process batch with rate limiting
            batch_embeddings = []
            for text in batch:
                embedding = await self.embed_text_async(text)
                batch_embeddings.append(embedding)

            embeddings.extend(batch_embeddings)

            logger.debug(
                "Batch progress",
                processed=min(i + self.BATCH_SIZE, len(texts)),
                total=len(texts),
            )

        logger.info(
            "Batch embeddings completed",
            total_embeddings=len(embeddings),
        )

        return embeddings

    @property
    def dimension(self) -> int:
        """Get embedding dimension."""
        return self._dimension


# Singleton instance
_embeddings_client: BedrockEmbeddings | None = None


def get_embeddings_client() -> BedrockEmbeddings:
    """Get Bedrock embeddings client singleton."""
    global _embeddings_client
    if _embeddings_client is None:
        _embeddings_client = BedrockEmbeddings()
    return _embeddings_client
