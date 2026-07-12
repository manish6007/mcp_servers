"""
Metadata-driven Data Quality rule framework.

Adding a new dataset to the DQ investigator should require only an entry in
`DQ_RULES` below -- no code changes to the server or the check logic. Any
table not present in `DQ_RULES` still works: `get_table_rules` returns a
best-effort default (using the DEFAULT_* constants) so ad-hoc tables can be
investigated immediately, while onboarded tables get precise, curated checks.
"""

from __future__ import annotations

from typing import Any, TypedDict


class TableRules(TypedDict, total=False):
    business_keys: list[str]
    date_column: str
    freshness_column: str
    freshness_threshold_hours: float
    critical_columns: list[str]
    null_anomaly_threshold_pct: float
    row_count_drop_warning_pct: float
    row_count_drop_critical_pct: float
    default_metric_column: str
    default_group_by_column: str


DEFAULT_DATE_COLUMN = "business_date"
DEFAULT_FRESHNESS_THRESHOLD_HOURS = 6.0
DEFAULT_NULL_ANOMALY_THRESHOLD_PCT = 5.0
DEFAULT_ROW_COUNT_DROP_WARNING_PCT = 10.0
DEFAULT_ROW_COUNT_DROP_CRITICAL_PCT = 30.0

# ---------------------------------------------------------------------------
# Dataset metadata. Extend this dict to onboard a new table -- no other
# source change is required.
# ---------------------------------------------------------------------------
DQ_RULES: dict[str, TableRules] = {
    "email_events": {
        "business_keys": ["email_id", "event_type", "event_timestamp"],
        "date_column": "business_date",
        "freshness_column": "event_timestamp",
        "freshness_threshold_hours": 6.0,
        "critical_columns": ["email_id", "campaign_id", "recipient_id", "event_type"],
        "null_anomaly_threshold_pct": 5.0,
        "row_count_drop_warning_pct": 10.0,
        "row_count_drop_critical_pct": 30.0,
        "default_metric_column": "email_id",
        "default_group_by_column": "campaign_id",
    },
    "email_sends": {
        "business_keys": ["email_id"],
        "date_column": "send_date",
        "freshness_column": "sent_timestamp",
        "freshness_threshold_hours": 4.0,
        "critical_columns": ["email_id", "campaign_id", "recipient_id"],
        "null_anomaly_threshold_pct": 5.0,
        "row_count_drop_warning_pct": 10.0,
        "row_count_drop_critical_pct": 30.0,
        "default_metric_column": "email_id",
        "default_group_by_column": "campaign_id",
    },
    "campaign_performance": {
        "business_keys": ["campaign_id", "business_date"],
        "date_column": "business_date",
        "freshness_column": "updated_at",
        "freshness_threshold_hours": 12.0,
        "critical_columns": ["campaign_id", "sends", "opens", "clicks"],
        "null_anomaly_threshold_pct": 5.0,
        "row_count_drop_warning_pct": 15.0,
        "row_count_drop_critical_pct": 40.0,
        "default_metric_column": "opens",
        "default_group_by_column": "campaign_id",
    },
}


def get_table_rules(table: str) -> TableRules:
    """
    Return the metadata rules for `table`.

    `table` may be a bare table name (e.g. "email_events") or a fully
    qualified `catalog.schema.table` -- lookups always key off the final
    path segment so the same metadata applies regardless of which
    catalog/schema the table lives in.
    """
    key = table.split(".")[-1]
    rules = DQ_RULES.get(key, {})
    return _with_defaults(rules)


def _with_defaults(rules: TableRules) -> TableRules:
    merged: TableRules = {
        "business_keys": rules.get("business_keys", []),
        "date_column": rules.get("date_column", DEFAULT_DATE_COLUMN),
        "freshness_column": rules.get("freshness_column", "event_timestamp"),
        "freshness_threshold_hours": rules.get(
            "freshness_threshold_hours", DEFAULT_FRESHNESS_THRESHOLD_HOURS
        ),
        "critical_columns": rules.get("critical_columns", []),
        "null_anomaly_threshold_pct": rules.get(
            "null_anomaly_threshold_pct", DEFAULT_NULL_ANOMALY_THRESHOLD_PCT
        ),
        "row_count_drop_warning_pct": rules.get(
            "row_count_drop_warning_pct", DEFAULT_ROW_COUNT_DROP_WARNING_PCT
        ),
        "row_count_drop_critical_pct": rules.get(
            "row_count_drop_critical_pct", DEFAULT_ROW_COUNT_DROP_CRITICAL_PCT
        ),
        "default_metric_column": rules.get("default_metric_column", ""),
        "default_group_by_column": rules.get("default_group_by_column", ""),
    }
    return merged


def list_known_tables() -> list[str]:
    return sorted(DQ_RULES.keys())


def register_table_rules(table: str, rules: TableRules) -> None:
    """
    Register/override rules for a table at runtime.

    Present for programmatic/test use; the supported way to onboard a
    dataset is still to add an entry to `DQ_RULES` above.
    """
    DQ_RULES[table] = rules


def rules_to_dict(table: str) -> dict[str, Any]:
    return dict(get_table_rules(table))
