"""Pydantic models shared across tools for structured, Claude-friendly output."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Status(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class QueryResult(BaseModel):
    """Structured result of a SQL execution."""

    status: Status
    row_count: int
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool = False
    execution_time_ms: int | None = None
    sql_executed: str | None = None
    error: str | None = None


class ColumnSchema(BaseModel):
    column_name: str
    data_type: str
    nullable: bool
    ordinal_position: int


class SchemaResult(BaseModel):
    status: Status
    catalog: str
    schema_name: str
    table: str
    columns: list[ColumnSchema]
    error: str | None = None


class SampleDataResult(BaseModel):
    status: Status
    table: str
    row_count: int
    columns: list[str]
    rows: list[dict[str, Any]]
    error: str | None = None


class RowCountComparisonResult(BaseModel):
    status: Status
    table: str
    current_date_value: str
    previous_date_value: str
    current_count: int
    previous_count: int
    difference: int
    percentage_change: float
    severity: Severity
    error: str | None = None


class ColumnNullStat(BaseModel):
    column: str
    null_percentage: float
    historical_average: float | None
    status: Severity


class NullAnomalyResult(BaseModel):
    status: Status
    table: str
    business_date: str
    results: list[ColumnNullStat]
    anomaly_count: int
    severity: Severity
    error: str | None = None


class DuplicateResult(BaseModel):
    status: Status
    table: str
    business_keys: list[str]
    business_date: str
    duplicate_group_count: int
    duplicate_record_count: int
    sample_duplicate_records: list[dict[str, Any]]
    severity: Severity
    error: str | None = None


class FreshnessResult(BaseModel):
    status: Status
    table: str
    timestamp_column: str
    max_timestamp: str | None
    delay_hours: float | None
    threshold_hours: float
    severity: Severity
    error: str | None = None


class MetricContributor(BaseModel):
    group_value: Any
    date1_value: float
    date2_value: float
    difference: float
    percentage_change: float | None


class MetricComparisonResult(BaseModel):
    status: Status
    table: str
    metric_column: str
    group_by_column: str | None
    date1: str
    date2: str
    date1_total: float
    date2_total: float
    total_difference: float
    total_percentage_change: float | None
    largest_contributors: list[MetricContributor]
    severity: Severity
    error: str | None = None


class FailedRecordsResult(BaseModel):
    status: Status
    table: str
    condition: str
    failed_count: int
    columns: list[str]
    sample_records: list[dict[str, Any]]
    error: str | None = None


class Finding(BaseModel):
    issue_type: str
    severity: Severity
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class DQReport(BaseModel):
    status: Status
    catalog: str
    schema_name: str
    table: str
    business_date: str
    summary: str
    overall_severity: Severity
    findings: list[Finding]
    recommended_next_investigations: list[str]
    error: str | None = None
