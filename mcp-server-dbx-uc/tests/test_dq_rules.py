from email_dq_mcp.dq_rules import get_table_rules


def test_known_table_returns_registered_metadata():
    rules = get_table_rules("email_events")
    assert rules["business_keys"] == ["email_id", "event_type", "event_timestamp"]
    assert rules["freshness_column"] == "event_timestamp"
    assert "campaign_id" in rules["critical_columns"]


def test_known_table_resolves_via_qualified_name():
    rules = get_table_rules("main.analytics.email_events")
    assert rules["freshness_column"] == "event_timestamp"


def test_unknown_table_returns_sane_defaults():
    rules = get_table_rules("some_new_table_nobody_registered")
    assert rules["date_column"] == "business_date"
    assert rules["business_keys"] == []
    assert rules["null_anomaly_threshold_pct"] > 0
