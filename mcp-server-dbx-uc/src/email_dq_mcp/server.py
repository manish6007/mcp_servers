"""
Email Analytics Data Quality MCP Server.

Exposes a set of investigation-oriented tools (not a generic Text2SQL
assistant) so Claude can act as a Data Quality Investigator over Databricks
Unity Catalog tables: run bounded read-only SQL, execute standardized DQ
checks (row counts, nulls, duplicates, freshness, metric drift), pull failed
records, and produce a consolidated root-cause investigation report.

Transport: stdio. The process is spawned by the MCP client (Claude Desktop,
Cursor, `uvx email-dq-mcp`, etc.) on demand and exits when the client closes
the connection -- there is no long-running service to manage.
"""

from __future__ import annotations

import time
from datetime import date as _date
from datetime import timedelta as _timedelta
from typing import Any

from mcp.server.fastmcp import FastMCP

from email_dq_mcp.config import ConfigurationError, configure_logging, load_settings
from email_dq_mcp.databricks_client import DatabricksClient, DatabricksClientError, StatementTimeoutError
from email_dq_mcp.dq_rules import get_table_rules
from email_dq_mcp.models import (
    ColumnNullStat,
    ColumnSchema,
    DQReport,
    DuplicateResult,
    FailedRecordsResult,
    Finding,
    FreshnessResult,
    MetricComparisonResult,
    MetricContributor,
    NullAnomalyResult,
    QueryResult,
    RowCountComparisonResult,
    SampleDataResult,
    SchemaResult,
    Severity,
    Status,
)
from email_dq_mcp.validators import (
    SQLValidationError,
    validate_and_prepare_sql,
    validate_identifier,
    validate_identifiers,
    validate_table_ref,
)

logger = configure_logging()
mcp = FastMCP("email-dq-mcp")

_settings = None
_client: DatabricksClient | None = None


def _get_client() -> DatabricksClient:
    """Lazily construct the Databricks client so import (e.g. `mcp dev`) never
    fails just because env vars aren't set yet, and configuration errors
    surface as a clean tool error instead of a crash at startup."""
    global _settings, _client
    if _client is None:
        _settings = load_settings()
        _client = DatabricksClient(_settings)
    return _client


def _run_sql(sql: str, tool_name: str, row_limit: int | None = None) -> Any:
    """
    Single choke point for every SQL statement this server executes,
    whether it originates from `execute_sql` or is built internally by a
    DQ check. Validates read-only-ness, guarantees a LIMIT, executes, and
    logs invocation/duration/status without ever logging result rows or
    the PAT.
    """
    client = _get_client()
    prepared = validate_and_prepare_sql(sql, row_limit or client._settings.default_row_limit)
    started = time.monotonic()
    logger.info("tool_invoked tool=%s", tool_name)
    try:
        outcome = client.execute_query(prepared)
    except (DatabricksClientError, StatementTimeoutError) as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "tool_failed tool=%s duration_ms=%d error=%s", tool_name, duration_ms, type(exc).__name__
        )
        raise
    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "tool_completed tool=%s duration_ms=%d rows=%d state=%s",
        tool_name, duration_ms, outcome.row_count, outcome.state,
    )
    return outcome


def _list_columns(table: str) -> list[str]:
    """List column names for a fully-qualified `catalog.schema.table` via
    Unity Catalog information_schema. Required by every check that needs
    to reason over "all columns" (e.g. null anomaly detection)."""
    parts = validate_table_ref(table).split(".")
    if len(parts) != 3:
        raise SQLValidationError(
            "This check requires a fully qualified table name "
            "('catalog.schema.table') so columns can be resolved via information_schema."
        )
    catalog, schema, tbl = parts
    sql = (
        f"SELECT column_name FROM {catalog}.information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{tbl}' "
        f"ORDER BY ordinal_position"
    )
    outcome = _run_sql(sql, "list_columns", row_limit=2000)
    return [row["column_name"] for row in outcome.rows]


def _severity_for_deviation(current: float, historical: float | None, threshold: float, tightened: bool) -> Severity:
    baseline = historical or 0.0
    diff = abs(current - baseline)
    warn_th = threshold / 2 if tightened else threshold
    crit_th = threshold if tightened else threshold * 2
    if current >= 50.0:
        return Severity.CRITICAL
    if diff >= crit_th:
        return Severity.CRITICAL
    if diff >= warn_th:
        return Severity.WARNING
    return Severity.OK


def _severity_for_pct_change(pct_change: float | None, warning_pct: float, critical_pct: float) -> Severity:
    if pct_change is None:
        return Severity.WARNING
    magnitude = abs(pct_change)
    if magnitude >= critical_pct:
        return Severity.CRITICAL
    if magnitude >= warning_pct:
        return Severity.WARNING
    return Severity.OK


def _max_severity(severities: list[Severity]) -> Severity:
    order = [Severity.OK, Severity.INFO, Severity.WARNING, Severity.CRITICAL]
    if not severities:
        return Severity.OK
    return max(severities, key=order.index)


# =============================================================================
# Tool: execute_sql
# =============================================================================


@mcp.tool()
def execute_sql(sql: str) -> dict[str, Any]:
    """
    Execute a read-only SQL SELECT statement against Databricks and return
    structured JSON results.

    Only SELECT / WITH ... SELECT statements are permitted. INSERT, UPDATE,
    DELETE, DROP, ALTER, TRUNCATE, MERGE, CREATE, GRANT, and REVOKE are
    rejected. A `LIMIT 1000` is automatically appended if the query does
    not already have one. Maximum execution time is 30 seconds.

    Args:
        sql: The SELECT statement to run.
    """
    try:
        outcome = _run_sql(sql, "execute_sql")
        return QueryResult(
            status=Status.SUCCESS,
            row_count=outcome.row_count,
            columns=outcome.columns,
            rows=outcome.rows,
            truncated=outcome.truncated,
            execution_time_ms=outcome.execution_time_ms,
            sql_executed=sql,
        ).model_dump()
    except (SQLValidationError, DatabricksClientError, StatementTimeoutError) as exc:
        return QueryResult(
            status=Status.ERROR, row_count=0, columns=[], rows=[], error=str(exc)
        ).model_dump()


# =============================================================================
# Tool: get_schema
# =============================================================================


def _get_schema_impl(catalog: str, schema: str, table: str) -> SchemaResult:
    validate_identifier(catalog, "catalog")
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")
    sql = (
        f"SELECT column_name, data_type, is_nullable, ordinal_position "
        f"FROM {catalog}.information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}' "
        f"ORDER BY ordinal_position"
    )
    outcome = _run_sql(sql, "get_schema", row_limit=2000)
    columns = [
        ColumnSchema(
            column_name=row["column_name"],
            data_type=row["data_type"],
            nullable=str(row.get("is_nullable", "YES")).upper() == "YES",
            ordinal_position=int(row["ordinal_position"]),
        )
        for row in outcome.rows
    ]
    if not columns:
        raise DatabricksClientError(
            f"No columns found for {catalog}.{schema}.{table}. "
            "Check the table exists and this warehouse has access to it."
        )
    return SchemaResult(status=Status.SUCCESS, catalog=catalog, schema_name=schema, table=table, columns=columns)


@mcp.tool()
def get_schema(catalog: str, schema: str, table: str) -> dict[str, Any]:
    """
    Return the column schema of a Unity Catalog table: column_name,
    data_type, nullable, and ordinal_position for every column.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema (database) name.
        table: Table name.
    """
    try:
        return _get_schema_impl(catalog, schema, table).model_dump()
    except (SQLValidationError, DatabricksClientError, StatementTimeoutError) as exc:
        return SchemaResult(
            status=Status.ERROR, catalog=catalog, schema_name=schema, table=table, columns=[], error=str(exc)
        ).model_dump()


# =============================================================================
# Tool: get_sample_data
# =============================================================================


@mcp.tool()
def get_sample_data(catalog: str, schema: str, table: str, limit: int = 20) -> dict[str, Any]:
    """
    Return sample rows from a table for eyeballing shape/values during an
    investigation.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema (database) name.
        table: Table name.
        limit: Number of rows to return (default 20, max 100).
    """
    qualified = f"{catalog}.{schema}.{table}"
    try:
        validate_identifier(catalog, "catalog")
        validate_identifier(schema, "schema")
        validate_identifier(table, "table")
        capped_limit = max(1, min(limit, 100))
        outcome = _run_sql(f"SELECT * FROM {qualified} LIMIT {capped_limit}", "get_sample_data", row_limit=capped_limit)
        return SampleDataResult(
            status=Status.SUCCESS, table=qualified, row_count=outcome.row_count,
            columns=outcome.columns, rows=outcome.rows,
        ).model_dump()
    except (SQLValidationError, DatabricksClientError, StatementTimeoutError) as exc:
        return SampleDataResult(
            status=Status.ERROR, table=qualified, row_count=0, columns=[], rows=[], error=str(exc)
        ).model_dump()


# =============================================================================
# Tool: compare_row_counts
# =============================================================================


def _compare_row_counts_impl(table: str, current_date: str, previous_date: str) -> RowCountComparisonResult:
    validate_table_ref(table)
    rules = get_table_rules(table)
    date_column = validate_identifier(rules["date_column"], "date_column")

    sql = (
        f"SELECT "
        f"SUM(CASE WHEN {date_column} = '{current_date}' THEN 1 ELSE 0 END) AS current_count, "
        f"SUM(CASE WHEN {date_column} = '{previous_date}' THEN 1 ELSE 0 END) AS previous_count "
        f"FROM {table} WHERE {date_column} IN ('{current_date}', '{previous_date}')"
    )
    outcome = _run_sql(sql, "compare_row_counts", row_limit=1)
    row = outcome.rows[0] if outcome.rows else {}
    current_count = int(row.get("current_count") or 0)
    previous_count = int(row.get("previous_count") or 0)
    difference = current_count - previous_count
    pct_change = (difference / previous_count * 100) if previous_count else (100.0 if current_count else 0.0)

    severity = _severity_for_pct_change(
        pct_change if pct_change <= 0 else 0.0,
        rules["row_count_drop_warning_pct"],
        rules["row_count_drop_critical_pct"],
    )

    return RowCountComparisonResult(
        status=Status.SUCCESS, table=table, current_date_value=current_date, previous_date_value=previous_date,
        current_count=current_count, previous_count=previous_count, difference=difference,
        percentage_change=round(pct_change, 2), severity=severity,
    )


@mcp.tool()
def compare_row_counts(table: str, current_date: str, previous_date: str) -> dict[str, Any]:
    """
    Compare row counts between two business dates for a table. Uses the
    table's configured date column (see dq_rules.py), defaulting to
    "business_date" for tables without registered metadata.

    Args:
        table: Fully qualified `catalog.schema.table` (or bare table name).
        current_date: Date to investigate, e.g. "2026-07-11".
        previous_date: Baseline date to compare against, e.g. "2026-07-10".
    """
    try:
        return _compare_row_counts_impl(table, current_date, previous_date).model_dump()
    except (SQLValidationError, DatabricksClientError, StatementTimeoutError) as exc:
        return RowCountComparisonResult(
            status=Status.ERROR, table=table, current_date_value=current_date, previous_date_value=previous_date,
            current_count=0, previous_count=0, difference=0, percentage_change=0.0, severity=Severity.INFO,
            error=str(exc),
        ).model_dump()


# =============================================================================
# Tool: detect_null_anomalies
# =============================================================================


def _detect_null_anomalies_impl(table: str, date_column: str, business_date: str) -> NullAnomalyResult:
    validate_table_ref(table)
    validate_identifier(date_column, "date_column")
    rules = get_table_rules(table)
    threshold = rules["null_anomaly_threshold_pct"]
    critical_columns = set(rules["critical_columns"])

    raw_columns = [c for c in _list_columns(table) if c != date_column]
    columns: list[str] = []
    for c in raw_columns:
        try:
            columns.append(validate_identifier(c, "column"))
        except SQLValidationError:
            continue  # skip columns whose names aren't safe to interpolate (e.g. nested/struct fields)
    if not columns:
        raise DatabricksClientError(f"Could not resolve any columns for {table} to check for nulls.")

    def null_stats_sql(where_clause: str) -> str:
        exprs = ", ".join(
            f"SUM(CASE WHEN `{c}` IS NULL THEN 1 ELSE 0 END) AS `{c}__nulls`" for c in columns
        )
        return f"SELECT COUNT(*) AS total_rows, {exprs} FROM {table} WHERE {where_clause}"

    current_outcome = _run_sql(
        null_stats_sql(f"{date_column} = '{business_date}'"), "detect_null_anomalies:current", row_limit=1
    )
    current_row = current_outcome.rows[0] if current_outcome.rows else {}
    total_rows = int(current_row.get("total_rows") or 0)

    lookback_days = _get_client()._settings.historical_lookback_days
    historical_outcome = _run_sql(
        null_stats_sql(
            f"{date_column} >= date_sub('{business_date}', {lookback_days}) "
            f"AND {date_column} < '{business_date}'"
        ),
        "detect_null_anomalies:historical",
        row_limit=1,
    )
    historical_row = historical_outcome.rows[0] if historical_outcome.rows else {}
    historical_total = int(historical_row.get("total_rows") or 0)

    results: list[ColumnNullStat] = []
    for c in columns:
        current_nulls = int(current_row.get(f"{c}__nulls") or 0)
        current_pct = (current_nulls / total_rows * 100) if total_rows else 0.0
        historical_avg = None
        if historical_total:
            historical_nulls = int(historical_row.get(f"{c}__nulls") or 0)
            historical_avg = round(historical_nulls / historical_total * 100, 2)
        status = _severity_for_deviation(current_pct, historical_avg, threshold, tightened=c in critical_columns)
        results.append(
            ColumnNullStat(
                column=c, null_percentage=round(current_pct, 2), historical_average=historical_avg, status=status
            )
        )

    anomaly_count = sum(1 for r in results if r.status != Severity.OK)
    overall = _max_severity([r.status for r in results])
    return NullAnomalyResult(
        status=Status.SUCCESS, table=table, business_date=business_date,
        results=results, anomaly_count=anomaly_count, severity=overall,
    )


@mcp.tool()
def detect_null_anomalies(table: str, date_column: str, business_date: str) -> dict[str, Any]:
    """
    For every column in the table, compute the null percentage on
    `business_date` and compare it against the historical average over the
    preceding lookback window, flagging anomalies. Columns registered as
    `critical_columns` in dq_rules.py use a tighter anomaly threshold.

    Args:
        table: Fully qualified `catalog.schema.table`.
        date_column: Column used to filter to a single business date.
        business_date: Date to investigate, e.g. "2026-07-11".
    """
    try:
        return _detect_null_anomalies_impl(table, date_column, business_date).model_dump()
    except (SQLValidationError, DatabricksClientError, StatementTimeoutError) as exc:
        return NullAnomalyResult(
            status=Status.ERROR, table=table, business_date=business_date,
            results=[], anomaly_count=0, severity=Severity.INFO, error=str(exc),
        ).model_dump()


# =============================================================================
# Tool: detect_duplicates
# =============================================================================


def _detect_duplicates_impl(table: str, business_keys: list[str], business_date: str) -> DuplicateResult:
    validate_table_ref(table)
    keys = validate_identifiers(business_keys, "business_keys")
    if not keys:
        raise SQLValidationError("business_keys must contain at least one column.")
    rules = get_table_rules(table)
    date_column = validate_identifier(rules["date_column"], "date_column")
    key_list = ", ".join(keys)

    summary_sql = (
        f"SELECT COUNT(*) AS group_count, SUM(cnt) AS record_count FROM ("
        f"SELECT {key_list}, COUNT(*) AS cnt FROM {table} "
        f"WHERE {date_column} = '{business_date}' GROUP BY {key_list} HAVING COUNT(*) > 1)"
    )
    summary_outcome = _run_sql(summary_sql, "detect_duplicates:summary", row_limit=1)
    summary_row = summary_outcome.rows[0] if summary_outcome.rows else {}
    group_count = int(summary_row.get("group_count") or 0)
    record_count = int(summary_row.get("record_count") or 0)

    sample_sql = (
        f"SELECT {key_list}, COUNT(*) AS duplicate_count FROM {table} "
        f"WHERE {date_column} = '{business_date}' GROUP BY {key_list} "
        f"HAVING COUNT(*) > 1 ORDER BY duplicate_count DESC LIMIT 20"
    )
    sample_outcome = _run_sql(sample_sql, "detect_duplicates:sample", row_limit=20)

    severity = Severity.OK if group_count == 0 else (Severity.CRITICAL if group_count >= 10 else Severity.WARNING)

    return DuplicateResult(
        status=Status.SUCCESS, table=table, business_keys=keys, business_date=business_date,
        duplicate_group_count=group_count, duplicate_record_count=record_count,
        sample_duplicate_records=sample_outcome.rows, severity=severity,
    )


@mcp.tool()
def detect_duplicates(table: str, business_keys: list[str], business_date: str) -> dict[str, Any]:
    """
    Detect duplicate records for `business_date` based on `business_keys`.
    Returns the number of duplicate key groups, total records involved, and
    a sample of the offending key combinations with their occurrence count.

    Args:
        table: Fully qualified `catalog.schema.table`.
        business_keys: Columns that should uniquely identify a record.
        business_date: Date to investigate, e.g. "2026-07-11".
    """
    try:
        return _detect_duplicates_impl(table, business_keys, business_date).model_dump()
    except (SQLValidationError, DatabricksClientError, StatementTimeoutError) as exc:
        return DuplicateResult(
            status=Status.ERROR, table=table, business_keys=business_keys, business_date=business_date,
            duplicate_group_count=0, duplicate_record_count=0, sample_duplicate_records=[],
            severity=Severity.INFO, error=str(exc),
        ).model_dump()


# =============================================================================
# Tool: detect_freshness_issues
# =============================================================================


def _detect_freshness_impl(table: str, timestamp_column: str, threshold_hours: float) -> FreshnessResult:
    validate_table_ref(table)
    validate_identifier(timestamp_column, "timestamp_column")

    sql = (
        f"SELECT CAST(MAX({timestamp_column}) AS STRING) AS max_timestamp, "
        f"(unix_timestamp(current_timestamp()) - unix_timestamp(MAX({timestamp_column}))) / 3600.0 AS delay_hours "
        f"FROM {table}"
    )
    outcome = _run_sql(sql, "detect_freshness_issues", row_limit=1)
    row = outcome.rows[0] if outcome.rows else {}
    max_ts = row.get("max_timestamp")
    delay_hours_raw = row.get("delay_hours")
    delay_hours = round(float(delay_hours_raw), 2) if delay_hours_raw is not None else None

    if max_ts is None or delay_hours is None or delay_hours > threshold_hours * 1.5:
        severity = Severity.CRITICAL
    elif delay_hours > threshold_hours:
        severity = Severity.WARNING
    else:
        severity = Severity.OK

    return FreshnessResult(
        status=Status.SUCCESS, table=table, timestamp_column=timestamp_column,
        max_timestamp=max_ts, delay_hours=delay_hours, threshold_hours=threshold_hours, severity=severity,
    )


@mcp.tool()
def detect_freshness_issues(table: str, timestamp_column: str, threshold_hours: float = 6.0) -> dict[str, Any]:
    """
    Check data freshness: how many hours old is the most recent record?

    Args:
        table: Fully qualified `catalog.schema.table`.
        timestamp_column: Column holding the event/load timestamp.
        threshold_hours: Delay threshold considered acceptable (default 6h).
    """
    try:
        return _detect_freshness_impl(table, timestamp_column, threshold_hours).model_dump()
    except (SQLValidationError, DatabricksClientError, StatementTimeoutError) as exc:
        return FreshnessResult(
            status=Status.ERROR, table=table, timestamp_column=timestamp_column, max_timestamp=None,
            delay_hours=None, threshold_hours=threshold_hours, severity=Severity.INFO, error=str(exc),
        ).model_dump()


# =============================================================================
# Tool: compare_metrics
# =============================================================================


def _compare_metrics_impl(
    table: str, metric_column: str, group_by_column: str, date1: str, date2: str
) -> MetricComparisonResult:
    validate_table_ref(table)
    validate_identifier(metric_column, "metric_column")
    validate_identifier(group_by_column, "group_by_column")
    rules = get_table_rules(table)
    date_column = validate_identifier(rules["date_column"], "date_column")

    totals_sql = (
        f"SELECT "
        f"SUM(CASE WHEN {date_column} = '{date1}' THEN {metric_column} ELSE 0 END) AS date1_total, "
        f"SUM(CASE WHEN {date_column} = '{date2}' THEN {metric_column} ELSE 0 END) AS date2_total "
        f"FROM {table} WHERE {date_column} IN ('{date1}', '{date2}')"
    )
    totals_outcome = _run_sql(totals_sql, "compare_metrics:totals", row_limit=1)
    totals_row = totals_outcome.rows[0] if totals_outcome.rows else {}
    date1_total = float(totals_row.get("date1_total") or 0)
    date2_total = float(totals_row.get("date2_total") or 0)
    total_diff = date2_total - date1_total
    total_pct = (total_diff / date1_total * 100) if date1_total else None

    breakdown_sql = (
        f"SELECT {group_by_column} AS group_value, "
        f"SUM(CASE WHEN {date_column} = '{date1}' THEN {metric_column} ELSE 0 END) AS date1_value, "
        f"SUM(CASE WHEN {date_column} = '{date2}' THEN {metric_column} ELSE 0 END) AS date2_value "
        f"FROM {table} WHERE {date_column} IN ('{date1}', '{date2}') "
        f"GROUP BY {group_by_column} ORDER BY ABS("
        f"SUM(CASE WHEN {date_column} = '{date2}' THEN {metric_column} ELSE 0 END) - "
        f"SUM(CASE WHEN {date_column} = '{date1}' THEN {metric_column} ELSE 0 END)) DESC "
        f"LIMIT 10"
    )
    breakdown_outcome = _run_sql(breakdown_sql, "compare_metrics:breakdown", row_limit=10)

    contributors = []
    for row in breakdown_outcome.rows:
        v1 = float(row.get("date1_value") or 0)
        v2 = float(row.get("date2_value") or 0)
        diff = v2 - v1
        pct = (diff / v1 * 100) if v1 else None
        contributors.append(
            MetricContributor(
                group_value=row.get("group_value"), date1_value=v1, date2_value=v2,
                difference=round(diff, 2), percentage_change=round(pct, 2) if pct is not None else None,
            )
        )

    severity = _severity_for_pct_change(total_pct, warning_pct=10.0, critical_pct=30.0)

    return MetricComparisonResult(
        status=Status.SUCCESS, table=table, metric_column=metric_column, group_by_column=group_by_column,
        date1=date1, date2=date2, date1_total=round(date1_total, 2), date2_total=round(date2_total, 2),
        total_difference=round(total_diff, 2),
        total_percentage_change=round(total_pct, 2) if total_pct is not None else None,
        largest_contributors=contributors, severity=severity,
    )


@mcp.tool()
def compare_metrics(table: str, metric_column: str, group_by_column: str, date1: str, date2: str) -> dict[str, Any]:
    """
    Compare a numeric metric between two dates, broken down by a grouping
    column (e.g. campaign_id), and surface the largest contributors to the
    overall movement.

    Args:
        table: Fully qualified `catalog.schema.table`.
        metric_column: Numeric column to aggregate (SUM).
        group_by_column: Column to break the comparison down by.
        date1: Baseline date, e.g. "2026-07-10".
        date2: Comparison date, e.g. "2026-07-11".
    """
    try:
        return _compare_metrics_impl(table, metric_column, group_by_column, date1, date2).model_dump()
    except (SQLValidationError, DatabricksClientError, StatementTimeoutError) as exc:
        return MetricComparisonResult(
            status=Status.ERROR, table=table, metric_column=metric_column, group_by_column=group_by_column,
            date1=date1, date2=date2, date1_total=0.0, date2_total=0.0, total_difference=0.0,
            total_percentage_change=None, largest_contributors=[], severity=Severity.INFO, error=str(exc),
        ).model_dump()


# =============================================================================
# Tool: get_failed_records
# =============================================================================


def _get_failed_records_impl(table: str, condition: str, limit: int) -> FailedRecordsResult:
    validate_table_ref(table)
    if not condition or not condition.strip():
        raise SQLValidationError("condition must not be empty.")
    capped_limit = max(1, min(limit, 1000))
    sql = f"SELECT * FROM {table} WHERE ({condition}) LIMIT {capped_limit}"
    outcome = _run_sql(sql, "get_failed_records", row_limit=capped_limit)
    return FailedRecordsResult(
        status=Status.SUCCESS, table=table, condition=condition, failed_count=outcome.row_count,
        columns=outcome.columns, sample_records=outcome.rows,
    )


@mcp.tool()
def get_failed_records(table: str, condition: str, limit: int = 100) -> dict[str, Any]:
    """
    Retrieve records that violate a data quality condition, e.g.
    "email_id IS NULL OR sent_timestamp IS NULL" or
    "opens > sends". The condition is inlined into a WHERE clause and is
    still subject to the read-only SQL safety checks.

    Args:
        table: Fully qualified `catalog.schema.table`.
        condition: SQL boolean expression describing the failure condition.
        limit: Max rows to return (default 100).
    """
    try:
        return _get_failed_records_impl(table, condition, limit).model_dump()
    except (SQLValidationError, DatabricksClientError, StatementTimeoutError) as exc:
        return FailedRecordsResult(
            status=Status.ERROR, table=table, condition=condition, failed_count=0, columns=[], sample_records=[],
            error=str(exc),
        ).model_dump()


# =============================================================================
# Tool: run_email_analytics_dq (primary investigator entry point)
# =============================================================================


@mcp.tool()
def run_email_analytics_dq(catalog: str, schema: str, table: str, business_date: str) -> dict[str, Any]:
    """
    Primary Data Quality Investigator tool. Automatically runs the full
    standard suite of checks for `business_date` -- row count validation,
    null checks, duplicate checks, freshness checks, metric trend checks,
    and a sample of failed records -- and returns a consolidated summary
    with severity, findings, and recommended next investigations for
    Claude to reason over.

    Args:
        catalog: Unity Catalog catalog name.
        schema: Schema (database) name.
        table: Table name (bare, not qualified).
        business_date: Date to investigate, e.g. "2026-07-11".
    """
    qualified = f"{catalog}.{schema}.{table}"
    rules = get_table_rules(table)
    findings: list[Finding] = []

    try:
        parsed_date = _date.fromisoformat(business_date)
        previous_date = (parsed_date - _timedelta(days=1)).isoformat()
    except ValueError:
        previous_date = business_date

    # 1. Row count validation
    try:
        row_counts = _compare_row_counts_impl(qualified, business_date, previous_date)
        if row_counts.severity != Severity.OK:
            findings.append(
                Finding(
                    issue_type="row_count_drop",
                    severity=row_counts.severity,
                    description=(
                        f"Row count changed {row_counts.percentage_change}% vs {previous_date} "
                        f"({row_counts.previous_count} -> {row_counts.current_count})."
                    ),
                    evidence=row_counts.model_dump(),
                )
            )
    except Exception as exc:
        findings.append(Finding(issue_type="row_count_check_error", severity=Severity.WARNING, description=str(exc)))

    # 2. Null checks
    try:
        null_result = _detect_null_anomalies_impl(qualified, rules["date_column"], business_date)
        if null_result.anomaly_count > 0:
            flagged = [r for r in null_result.results if r.status != Severity.OK]
            findings.append(
                Finding(
                    issue_type="null_anomaly",
                    severity=null_result.severity,
                    description=f"{len(flagged)} column(s) show abnormal null rates on {business_date}.",
                    evidence={"flagged_columns": [f.model_dump() for f in flagged]},
                )
            )
    except Exception as exc:
        findings.append(Finding(issue_type="null_check_error", severity=Severity.WARNING, description=str(exc)))

    # 3. Duplicate checks
    try:
        business_keys = rules["business_keys"] or [rules["date_column"]]
        dup_result = _detect_duplicates_impl(qualified, business_keys, business_date)
        if dup_result.severity != Severity.OK:
            findings.append(
                Finding(
                    issue_type="duplicate_records",
                    severity=dup_result.severity,
                    description=(
                        f"{dup_result.duplicate_group_count} duplicate key group(s) "
                        f"({dup_result.duplicate_record_count} records) found on {business_date}."
                    ),
                    evidence={"sample_duplicate_records": dup_result.sample_duplicate_records[:5]},
                )
            )
    except Exception as exc:
        findings.append(Finding(issue_type="duplicate_check_error", severity=Severity.WARNING, description=str(exc)))

    # 4. Freshness checks
    try:
        freshness = _detect_freshness_impl(
            qualified, rules["freshness_column"], rules["freshness_threshold_hours"]
        )
        if freshness.severity != Severity.OK:
            findings.append(
                Finding(
                    issue_type="freshness_issue",
                    severity=freshness.severity,
                    description=(
                        f"Latest {rules['freshness_column']} is {freshness.delay_hours}h old "
                        f"(threshold {freshness.threshold_hours}h)."
                    ),
                    evidence=freshness.model_dump(),
                )
            )
    except Exception as exc:
        findings.append(Finding(issue_type="freshness_check_error", severity=Severity.WARNING, description=str(exc)))

    # 5. Metric trend checks
    if rules["default_metric_column"] and rules["default_group_by_column"]:
        try:
            metric_result = _compare_metrics_impl(
                qualified, rules["default_metric_column"], rules["default_group_by_column"],
                previous_date, business_date,
            )
            if metric_result.severity != Severity.OK:
                findings.append(
                    Finding(
                        issue_type="metric_trend_deviation",
                        severity=metric_result.severity,
                        description=(
                            f"{rules['default_metric_column']} moved "
                            f"{metric_result.total_percentage_change}% vs {previous_date}."
                        ),
                        evidence={
                            "date1_total": metric_result.date1_total,
                            "date2_total": metric_result.date2_total,
                            "largest_contributors": [c.model_dump() for c in metric_result.largest_contributors[:5]],
                        },
                    )
                )
        except Exception as exc:
            findings.append(Finding(issue_type="metric_check_error", severity=Severity.WARNING, description=str(exc)))

    # 6. Sample failed records for critical columns (nulls in critical columns)
    sample_failures: dict[str, Any] = {}
    if rules["critical_columns"]:
        try:
            null_condition = " OR ".join(f"{c} IS NULL" for c in rules["critical_columns"])
            failed = _get_failed_records_impl(qualified, null_condition, 20)
            if failed.failed_count > 0:
                sample_failures = {"condition": null_condition, "sample_records": failed.sample_records}
                findings.append(
                    Finding(
                        issue_type="critical_column_nulls",
                        severity=Severity.CRITICAL if failed.failed_count > 0 else Severity.OK,
                        description=(
                            f"{failed.failed_count} sampled record(s) have NULLs in a critical column "
                            f"({', '.join(rules['critical_columns'])})."
                        ),
                        evidence=sample_failures,
                    )
                )
        except Exception as exc:
            findings.append(Finding(issue_type="failed_records_error", severity=Severity.WARNING, description=str(exc)))

    overall_severity = _max_severity([f.severity for f in findings]) if findings else Severity.OK

    recommendations = _build_recommendations(findings, rules)

    if not findings:
        summary = f"No data quality issues detected for {qualified} on {business_date}."
    else:
        issue_types = ", ".join(sorted({f.issue_type for f in findings}))
        summary = (
            f"{len(findings)} finding(s) for {qualified} on {business_date} "
            f"(overall severity: {overall_severity.value}). Issue types: {issue_types}."
        )

    return DQReport(
        status=Status.SUCCESS, catalog=catalog, schema_name=schema, table=table, business_date=business_date,
        summary=summary, overall_severity=overall_severity, findings=findings,
        recommended_next_investigations=recommendations,
    ).model_dump()


def _build_recommendations(findings: list[Finding], rules: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    issue_types = {f.issue_type for f in findings}
    if "row_count_drop" in issue_types:
        recs.append(
            "Investigate upstream ingestion/pipeline job status for the affected business date "
            "(late-arriving data, failed job run, or upstream source outage)."
        )
    if "null_anomaly" in issue_types or "critical_column_nulls" in issue_types:
        recs.append(
            "Use get_failed_records to pull the specific rows with unexpected NULLs and trace them "
            "back to the source system or upstream transformation that populates those columns."
        )
    if "duplicate_records" in issue_types:
        recs.append(
            "Use detect_duplicates output to inspect sample_duplicate_records and check the ingestion "
            "job for retry/reprocessing logic that may be double-writing records."
        )
    if "freshness_issue" in issue_types:
        recs.append(
            "Check the scheduling/orchestration system for the job that loads this table -- it may be "
            "delayed, stuck, or failing silently."
        )
    if "metric_trend_deviation" in issue_types:
        recs.append(
            "Use compare_metrics' largest_contributors to identify which campaigns/segments are driving "
            "the movement, then use get_failed_records to validate those specific records."
        )
    if not recs:
        recs.append("No anomalies detected; no further investigation required at this time.")
    return recs


def main() -> None:
    """Entry point for `uvx email-dq-mcp` / the `email-dq-mcp` console script."""
    try:
        load_settings()
    except ConfigurationError as exc:
        logger.error("startup_configuration_error error=%s", exc)
        raise SystemExit(f"email-dq-mcp: {exc}") from exc
    mcp.run()


if __name__ == "__main__":
    main()
