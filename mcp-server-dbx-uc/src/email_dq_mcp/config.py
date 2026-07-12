"""
Configuration and logging setup for the Email Data Quality MCP Server.

Settings are loaded from environment variables (optionally via a local
.env file). PAT values are never logged or exposed in error messages.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

load_dotenv()


class Settings(BaseModel):
    """Runtime configuration for connecting to Databricks SQL Warehouses."""

    databricks_host: str = Field(...)
    databricks_pat: str = Field(...)
    databricks_warehouse_id: str = Field(...)

    query_timeout_seconds: int = Field(default=30, le=30, gt=0)
    default_row_limit: int = Field(default=1000, gt=0)
    max_sample_rows: int = Field(default=100, gt=0)
    historical_lookback_days: int = Field(default=7, gt=0)

    @field_validator("databricks_host")
    @classmethod
    def _normalize_host(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("DATABRICKS_HOST must not be empty")
        # Statement Execution API calls are built by this client as
        # f"{host}/api/2.0/...", so strip any trailing slash and a
        # scheme prefix the user may have pasted in by mistake.
        value = value.rstrip("/")
        if not value.startswith("https://"):
            value = f"https://{value}"
        return value

    @field_validator("databricks_pat", "databricks_warehouse_id")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()


class ConfigurationError(RuntimeError):
    """Raised when required environment variables are missing or invalid."""


def load_settings() -> Settings:
    try:
        return Settings(
            databricks_host=_require_env("DATABRICKS_HOST"),
            databricks_pat=_require_env("DATABRICKS_PAT"),
            databricks_warehouse_id=_require_env("DATABRICKS_WAREHOUSE_ID"),
            query_timeout_seconds=int(os.environ.get("QUERY_TIMEOUT_SECONDS", "30")),
            default_row_limit=int(os.environ.get("DEFAULT_ROW_LIMIT", "1000")),
            max_sample_rows=int(os.environ.get("MAX_SAMPLE_ROWS", "100")),
            historical_lookback_days=int(os.environ.get("HISTORICAL_LOOKBACK_DAYS", "7")),
        )
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigurationError(
            f"Missing required environment variable: {name}. "
            "Set it in your environment or in a .env file."
        )
    return value


class _RedactPatFilter(logging.Filter):
    """Belt-and-suspenders filter: strips the PAT if it ever ends up in a log record."""

    def __init__(self, pat: str | None) -> None:
        super().__init__()
        self._pat = pat

    def filter(self, record: logging.LogRecord) -> bool:
        if self._pat:
            message = record.getMessage()
            if self._pat in message:
                record.msg = message.replace(self._pat, "***REDACTED***")
                record.args = ()
        return True


def configure_logging() -> logging.Logger:
    """
    Configure logging to stderr (stdout is reserved for MCP stdio transport).

    Only tool invocations, durations, and statement status are logged.
    Full result payloads and PAT values are never logged. The redaction
    filter reads DATABRICKS_PAT directly from the environment so it is
    effective regardless of which module triggers logging setup first.
    """
    logger = logging.getLogger("email_dq_mcp")
    if logger.handlers:
        return logger

    logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    handler.addFilter(_RedactPatFilter(os.environ.get("DATABRICKS_PAT")))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
