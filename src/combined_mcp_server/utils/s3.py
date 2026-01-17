"""
S3 utilities for file operations.

Provides functions for listing, downloading, and uploading files to S3.
"""

import gzip
import json
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Iterator

import boto3
from botocore.exceptions import ClientError

from combined_mcp_server.config import get_settings
from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)


class S3Error(Exception):
    """Custom exception for S3 operations."""

    pass


class S3Client:
    """
    S3 client for file operations.

    Provides methods for listing, downloading, and uploading files.
    """

    def __init__(self) -> None:
        """Initialize S3 client."""
        settings = get_settings()

        client_kwargs: dict[str, Any] = {
            "service_name": "s3",
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

        self._client = boto3.client(**client_kwargs)
        logger.info("S3 client initialized", region=settings.aws.region)

    def list_files(
        self,
        bucket: str,
        prefix: str = "",
        suffix: str = "",
        max_keys: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """
        List files in an S3 bucket with optional filtering.

        Args:
            bucket: S3 bucket name
            prefix: Key prefix to filter by
            suffix: Key suffix to filter by (e.g., '.md')
            max_keys: Maximum number of keys to return

        Yields:
            File metadata dictionaries with 'key', 'size', 'last_modified'

        Raises:
            S3Error: If listing fails
        """
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            page_iterator = paginator.paginate(
                Bucket=bucket,
                Prefix=prefix,
                PaginationConfig={"MaxItems": max_keys},
            )

            count = 0
            for page in page_iterator:
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if suffix and not key.endswith(suffix):
                        continue
                    yield {
                        "key": key,
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                    }
                    count += 1

            logger.info(
                "Listed S3 files",
                bucket=bucket,
                prefix=prefix,
                suffix=suffix,
                count=count,
            )

        except ClientError as e:
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(
                "Failed to list S3 files",
                bucket=bucket,
                prefix=prefix,
                error=error_message,
            )
            raise S3Error(f"Failed to list files in s3://{bucket}/{prefix}: {error_message}") from e

    def download_file(self, bucket: str, key: str) -> bytes:
        """
        Download a file from S3.

        Args:
            bucket: S3 bucket name
            key: S3 object key

        Returns:
            File contents as bytes

        Raises:
            S3Error: If download fails
        """
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read()
            logger.debug("Downloaded S3 file", bucket=bucket, key=key, size=len(content))
            return content

        except ClientError as e:
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(
                "Failed to download S3 file",
                bucket=bucket,
                key=key,
                error=error_message,
            )
            raise S3Error(f"Failed to download s3://{bucket}/{key}: {error_message}") from e

    def download_text(self, bucket: str, key: str, encoding: str = "utf-8") -> str:
        """
        Download a text file from S3.

        Args:
            bucket: S3 bucket name
            key: S3 object key
            encoding: Text encoding (default: utf-8)

        Returns:
            File contents as string

        Raises:
            S3Error: If download fails
        """
        content = self.download_file(bucket, key)
        return content.decode(encoding)

    def upload_json(
        self,
        bucket: str,
        key: str,
        data: Any,
        compress: bool = True,
    ) -> str:
        """
        Upload JSON data to S3.

        Args:
            bucket: S3 bucket name
            key: S3 object key
            data: Data to serialize as JSON
            compress: Whether to gzip compress the data

        Returns:
            S3 URI of the uploaded file

        Raises:
            S3Error: If upload fails
        """
        try:
            json_bytes = json.dumps(data, default=str, indent=2).encode("utf-8")

            if compress:
                buffer = BytesIO()
                with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
                    gz.write(json_bytes)
                body = buffer.getvalue()
                content_type = "application/gzip"
                if not key.endswith(".gz"):
                    key = f"{key}.gz"
            else:
                body = json_bytes
                content_type = "application/json"

            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )

            s3_uri = f"s3://{bucket}/{key}"
            logger.info("Uploaded JSON to S3", uri=s3_uri, size=len(body), compressed=compress)
            return s3_uri

        except ClientError as e:
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(
                "Failed to upload to S3",
                bucket=bucket,
                key=key,
                error=error_message,
            )
            raise S3Error(f"Failed to upload to s3://{bucket}/{key}: {error_message}") from e

    def generate_presigned_url(
        self,
        bucket: str,
        key: str,
        expiration_seconds: int = 3600,
    ) -> str:
        """
        Generate a presigned URL for downloading a file.

        Args:
            bucket: S3 bucket name
            key: S3 object key
            expiration_seconds: URL expiration time in seconds

        Returns:
            Presigned URL string

        Raises:
            S3Error: If URL generation fails
        """
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expiration_seconds,
            )
            logger.debug(
                "Generated presigned URL",
                bucket=bucket,
                key=key,
                expires_in=expiration_seconds,
            )
            return url

        except ClientError as e:
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(
                "Failed to generate presigned URL",
                bucket=bucket,
                key=key,
                error=error_message,
            )
            raise S3Error(
                f"Failed to generate presigned URL for s3://{bucket}/{key}: {error_message}"
            ) from e


def generate_result_key(prefix: str, query_hash: str) -> str:
    """
    Generate a unique S3 key for query results.

    Args:
        prefix: S3 prefix for results
        query_hash: Hash of the query for identification

    Returns:
        S3 key for the result file
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    return f"{prefix}{timestamp}/{query_hash}_{unique_id}.json"


# Singleton instance
_s3_client: S3Client | None = None


def get_s3_client() -> S3Client:
    """Get S3 client singleton."""
    global _s3_client
    if _s3_client is None:
        _s3_client = S3Client()
    return _s3_client
