"""
AWS Secrets Manager client for retrieving and caching secrets.

Provides secure credential management with automatic caching.
"""

import json
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import ClientError

from combined_mcp_server.config import get_settings
from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)


class SecretsManagerError(Exception):
    """Custom exception for Secrets Manager operations."""

    pass


class SecretsManager:
    """
    AWS Secrets Manager client with caching.

    Retrieves secrets from AWS Secrets Manager and caches them
    to minimize API calls.
    """

    def __init__(self) -> None:
        """Initialize Secrets Manager client."""
        settings = get_settings()

        client_kwargs: dict[str, Any] = {
            "service_name": "secretsmanager",
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
        self._cache: dict[str, dict[str, Any]] = {}
        logger.info("Secrets Manager client initialized", region=settings.aws.region)

    def get_secret(self, secret_name: str, force_refresh: bool = False) -> dict[str, Any]:
        """
        Retrieve a secret from Secrets Manager.

        Args:
            secret_name: Name or ARN of the secret
            force_refresh: Force refresh from Secrets Manager (bypass cache)

        Returns:
            Parsed secret value as dictionary

        Raises:
            SecretsManagerError: If secret retrieval fails
        """
        # Check cache first
        if not force_refresh and secret_name in self._cache:
            logger.debug("Returning cached secret", secret_name=secret_name)
            return self._cache[secret_name]

        try:
            logger.info("Retrieving secret from Secrets Manager", secret_name=secret_name)
            response = self._client.get_secret_value(SecretId=secret_name)

            # Parse secret string as JSON
            secret_string = response.get("SecretString")
            if not secret_string:
                raise SecretsManagerError(
                    f"Secret '{secret_name}' does not contain a string value"
                )

            secret_value = json.loads(secret_string)
            self._cache[secret_name] = secret_value

            logger.info("Secret retrieved successfully", secret_name=secret_name)
            return secret_value

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error(
                "Failed to retrieve secret",
                secret_name=secret_name,
                error_code=error_code,
                error_message=error_message,
            )
            raise SecretsManagerError(
                f"Failed to retrieve secret '{secret_name}': {error_message}"
            ) from e
        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse secret as JSON",
                secret_name=secret_name,
                error=str(e),
            )
            raise SecretsManagerError(
                f"Secret '{secret_name}' is not valid JSON: {e}"
            ) from e

    def clear_cache(self) -> None:
        """Clear the secret cache."""
        self._cache.clear()
        logger.info("Secret cache cleared")


@lru_cache
def get_secrets_manager() -> SecretsManager:
    """Get cached Secrets Manager instance."""
    return SecretsManager()
