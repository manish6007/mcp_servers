"""
Reusable client for the Databricks SQL Statement Execution API.

https://docs.databricks.com/api/workspace/statementexecution

Only SELECT statements should ever reach this client -- callers are
expected to have already run the SQL through `validators.py`. This module
is deliberately unaware of DQ semantics; it only knows how to submit a
statement, poll it to completion, and materialize results as rows of
plain Python values.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from email_dq_mcp.config import Settings, configure_logging

logger = configure_logging()

STATEMENTS_PATH = "/api/2.0/sql/statements"

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class DatabricksClientError(RuntimeError):
    """Raised for any failure talking to Databricks (network, auth, statement failure)."""


class StatementTimeoutError(DatabricksClientError):
    """Raised when a statement does not reach a terminal state within the deadline."""


@dataclass
class QueryOutcome:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    execution_time_ms: int
    statement_id: str
    state: str


@dataclass
class _RetryConfig:
    max_attempts: int = 4
    backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0


class DatabricksClient:
    """Thin, retrying HTTP client for the Statement Execution API."""

    def __init__(self, settings: Settings, retry: _RetryConfig | None = None) -> None:
        self._settings = settings
        self._retry = retry or _RetryConfig()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {settings.databricks_pat}",
                "Content-Type": "application/json",
            }
        )

    # -- low-level HTTP -----------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self._settings.databricks_host}{path}"
        attempt = 0
        delay = self._retry.backoff_seconds
        last_error: Exception | None = None

        while attempt < self._retry.max_attempts:
            attempt += 1
            try:
                response = self._session.request(method, url, timeout=self._settings.query_timeout_seconds + 10, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "databricks_http_error attempt=%d method=%s path=%s error=%s",
                    attempt, method, path, type(exc).__name__,
                )
                if attempt >= self._retry.max_attempts:
                    break
                time.sleep(delay)
                delay *= self._retry.backoff_multiplier
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._retry.max_attempts:
                logger.warning(
                    "databricks_retryable_status attempt=%d method=%s path=%s status=%d",
                    attempt, method, path, response.status_code,
                )
                time.sleep(delay)
                delay *= self._retry.backoff_multiplier
                continue

            if not response.ok:
                raise DatabricksClientError(
                    f"Databricks API error {response.status_code} on {method} {path}: "
                    f"{_safe_error_body(response)}"
                )
            return response.json() if response.content else {}

        raise DatabricksClientError(
            f"Databricks API request failed after {attempt} attempts: {last_error}"
        )

    # -- Statement Execution API ---------------------------------------------

    def execute_statement(self, sql: str, wait_timeout_seconds: int | None = None) -> dict[str, Any]:
        """Submit a statement for execution. Returns the initial statement payload."""
        timeout = wait_timeout_seconds or self._settings.query_timeout_seconds
        # Databricks caps synchronous wait at 50s; clamp and rely on our own poll loop.
        wait_clause = f"{min(max(timeout, 5), 50)}s"
        payload = {
            "warehouse_id": self._settings.databricks_warehouse_id,
            "statement": sql,
            "wait_timeout": wait_clause,
            "on_wait_timeout": "CONTINUE",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
        logger.info("statement_submit warehouse_id=%s", self._settings.databricks_warehouse_id)
        return self._request("POST", STATEMENTS_PATH, json=payload)

    def poll_statement(self, statement_id: str) -> dict[str, Any]:
        """Fetch the current status/result payload for a statement."""
        return self._request("GET", f"{STATEMENTS_PATH}/{statement_id}")

    def cancel_statement(self, statement_id: str) -> None:
        try:
            self._request("POST", f"{STATEMENTS_PATH}/{statement_id}/cancel")
        except DatabricksClientError as exc:
            logger.warning("statement_cancel_failed statement_id=%s error=%s", statement_id, exc)

    def fetch_results(self, statement_id: str, statement_payload: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]], bool]:
        """
        Materialize INLINE result chunks into (columns, rows, truncated).

        Handles multi-chunk results by following `next_chunk_index` links.
        """
        manifest = statement_payload.get("manifest", {})
        schema_columns = manifest.get("schema", {}).get("columns", [])
        columns = [c.get("name", f"col_{i}") for i, c in enumerate(schema_columns)]

        rows: list[dict[str, Any]] = []
        result = statement_payload.get("result", {}) or {}
        rows.extend(_rows_from_chunk(result, columns))

        next_chunk_index = result.get("next_chunk_index")
        total_chunk_count = manifest.get("total_chunk_count", 1)
        seen_chunks = 1 if result else 0

        while next_chunk_index is not None and seen_chunks < total_chunk_count:
            chunk_payload = self._request(
                "GET", f"{STATEMENTS_PATH}/{statement_id}/result/chunks/{next_chunk_index}"
            )
            rows.extend(_rows_from_chunk(chunk_payload, columns))
            next_chunk_index = chunk_payload.get("next_chunk_index")
            seen_chunks += 1

        truncated = bool(manifest.get("truncated", False))
        return columns, rows, truncated

    # -- orchestration --------------------------------------------------------

    def execute_query(self, sql: str, timeout_seconds: int | None = None) -> QueryOutcome:
        """
        Execute `sql` end-to-end: submit, poll until terminal, fetch and
        materialize rows. Raises DatabricksClientError / StatementTimeoutError
        on failure.
        """
        timeout = timeout_seconds or self._settings.query_timeout_seconds
        start = time.monotonic()

        payload = self.execute_statement(sql, wait_timeout_seconds=timeout)
        statement_id = payload["statement_id"]
        state = payload.get("status", {}).get("state", "PENDING")

        poll_interval = 0.5
        while state not in TERMINAL_STATES:
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                self.cancel_statement(statement_id)
                raise StatementTimeoutError(
                    f"Statement {statement_id} did not complete within {timeout}s (last state={state})."
                )
            time.sleep(min(poll_interval, timeout - elapsed))
            poll_interval = min(poll_interval * 1.5, 2.0)
            payload = self.poll_statement(statement_id)
            state = payload.get("status", {}).get("state", state)
            logger.info("statement_poll statement_id=%s state=%s", statement_id, state)

        execution_time_ms = int((time.monotonic() - start) * 1000)

        if state == "FAILED":
            error = payload.get("status", {}).get("error", {})
            message = error.get("message", "Unknown error")
            raise DatabricksClientError(f"Statement failed: {message}")
        if state == "CANCELED":
            raise DatabricksClientError(f"Statement {statement_id} was canceled (timeout or explicit cancel).")
        if state == "CLOSED":
            raise DatabricksClientError(f"Statement {statement_id} result is no longer available (CLOSED).")

        columns, rows, truncated = self.fetch_results(statement_id, payload)

        logger.info(
            "statement_complete statement_id=%s state=%s rows=%d duration_ms=%d",
            statement_id, state, len(rows), execution_time_ms,
        )

        return QueryOutcome(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            execution_time_ms=execution_time_ms,
            statement_id=statement_id,
            state=state,
        )


def _rows_from_chunk(chunk: dict[str, Any], columns: list[str]) -> list[dict[str, Any]]:
    data_array = chunk.get("data_array")
    if data_array is None:
        return []
    return [dict(zip(columns, row, strict=False)) for row in data_array]


def _safe_error_body(response: requests.Response) -> str:
    try:
        body = response.json()
        # Never let response bodies leak an Authorization header/token if the
        # API ever echoes request context back in an error payload.
        body.pop("Authorization", None)
        return str(body)
    except ValueError:
        return response.text[:500]
