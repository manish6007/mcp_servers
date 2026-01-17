"""
Configuration management using pydantic-settings.

Loads configuration from environment variables with validation and defaults.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env file at module import
_env_file = Path.cwd() / ".env"
if _env_file.exists():
    load_dotenv(_env_file)


class AWSSettings(BaseSettings):
    """AWS-related configuration."""

    model_config = SettingsConfigDict(env_prefix="AWS_", extra="ignore")

    region: str = Field(default="us-east-1", description="AWS region")
    access_key_id: str | None = Field(default=None, description="AWS access key ID")
    secret_access_key: SecretStr | None = Field(
        default=None, description="AWS secret access key"
    )
    endpoint_url: str | None = Field(
        default=None, description="Custom AWS endpoint (e.g., LocalStack)"
    )


class RedshiftSettings(BaseSettings):
    """Redshift-related configuration."""

    model_config = SettingsConfigDict(env_prefix="REDSHIFT_", extra="ignore")

    cluster_id: str = Field(default="", description="Redshift cluster identifier")
    database: str = Field(default="", description="Redshift database name")
    host: str = Field(default="", description="Redshift cluster endpoint")
    port: int = Field(default=5439, description="Redshift port")
    results_bucket: str = Field(default="", description="S3 bucket for large query results")
    results_prefix: str = Field(
        default="query-results/", description="S3 prefix for query results"
    )

    @property
    def is_configured(self) -> bool:
        """Check if Redshift is properly configured."""
        return bool(self.cluster_id and self.database and self.host and self.results_bucket)


class KnowledgebaseSettings(BaseSettings):
    """Knowledgebase-related configuration."""

    model_config = SettingsConfigDict(env_prefix="KNOWLEDGEBASE_", extra="ignore")

    s3_bucket: str = Field(default="", description="S3 bucket containing knowledge files")
    s3_prefix: str = Field(default="docs/", description="S3 prefix for knowledge files")


class PostgresSettings(BaseSettings):
    """PostgreSQL/pgvector configuration."""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")

    # Direct connection settings (used if secret_name is not set)
    host: str | None = Field(default=None, description="PostgreSQL host")
    port: int = Field(default=5432, description="PostgreSQL port")
    database: str | None = Field(default=None, description="PostgreSQL database")
    user: str | None = Field(default=None, description="PostgreSQL user")
    password: SecretStr | None = Field(default=None, description="PostgreSQL password")
    secret_name: str | None = Field(
        default=None, description="Secrets Manager secret name for credentials"
    )

    @property
    def use_secrets_manager(self) -> bool:
        """Check if Secrets Manager should be used for credentials."""
        return self.secret_name is not None


class BedrockSettings(BaseSettings):
    """AWS Bedrock configuration."""

    model_config = SettingsConfigDict(env_prefix="BEDROCK_", extra="ignore")

    embedding_model: str = Field(
        default="amazon.titan-embed-text-v2:0",
        description="Bedrock embedding model ID",
    )
    region: str = Field(default="us-east-1", description="Bedrock region")


class VectorStoreSettings(BaseSettings):
    """Vector store configuration."""

    model_config = SettingsConfigDict(env_prefix="VECTORSTORE_", extra="ignore")

    table_name: str = Field(default="kb_documents", description="Vector store table name")
    embedding_dimension: int = Field(
        default=1024, description="Embedding vector dimension"
    )


class HybridSearchSettings(BaseSettings):
    """Hybrid search configuration."""

    model_config = SettingsConfigDict(env_prefix="HYBRID_", extra="ignore")

    semantic_weight: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Weight for semantic search"
    )
    keyword_weight: float = Field(
        default=0.3, ge=0.0, le=1.0, description="Weight for keyword search"
    )
    rrf_k: int = Field(
        default=60, ge=1, description="RRF constant for rank fusion"
    )

    @field_validator("keyword_weight")
    @classmethod
    def validate_weights_sum(cls, v: float, info) -> float:
        """Validate that weights sum to approximately 1.0."""
        semantic = info.data.get("semantic_weight", 0.7)
        if abs(semantic + v - 1.0) > 0.01:
            raise ValueError(
                f"semantic_weight ({semantic}) + keyword_weight ({v}) should equal 1.0"
            )
        return v


class QueryCacheSettings(BaseSettings):
    """Query cache configuration."""

    model_config = SettingsConfigDict(env_prefix="QUERY_CACHE_", extra="ignore")

    size: int = Field(default=30, ge=1, le=1000, description="Max items in query cache")


class HealthCheckSettings(BaseSettings):
    """Health check server configuration."""

    model_config = SettingsConfigDict(env_prefix="HEALTH_CHECK_", extra="ignore")

    port: int = Field(default=8080, description="Health check server port")
    host: str = Field(default="0.0.0.0", description="Health check server host")


class LoggingSettings(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(env_prefix="LOG_", extra="ignore")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Log level"
    )
    format: Literal["json", "console"] = Field(
        default="json", description="Log format"
    )


class Settings(BaseSettings):
    """Main settings aggregating all configuration sections."""

    model_config = SettingsConfigDict(extra="ignore")

    aws: AWSSettings = Field(default_factory=AWSSettings)
    redshift: RedshiftSettings = Field(default_factory=RedshiftSettings)
    knowledgebase: KnowledgebaseSettings = Field(default_factory=KnowledgebaseSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    bedrock: BedrockSettings = Field(default_factory=BedrockSettings)
    vectorstore: VectorStoreSettings = Field(default_factory=VectorStoreSettings)
    hybrid_search: HybridSearchSettings = Field(default_factory=HybridSearchSettings)
    query_cache: QueryCacheSettings = Field(default_factory=QueryCacheSettings)
    health_check: HealthCheckSettings = Field(default_factory=HealthCheckSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
