import pytest

from email_dq_mcp.validators import (
    SQLValidationError,
    ensure_limit,
    validate_and_prepare_sql,
    validate_identifier,
    validate_read_only,
    validate_table_ref,
)


def test_allows_plain_select():
    validate_read_only("SELECT * FROM catalog.schema.table")


def test_allows_with_cte():
    validate_read_only("WITH x AS (SELECT 1) SELECT * FROM x")


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE catalog.schema.table",
        "DELETE FROM catalog.schema.table",
        "INSERT INTO catalog.schema.table VALUES (1)",
        "UPDATE catalog.schema.table SET x = 1",
        "SELECT * FROM t; DROP TABLE t",
        "CREATE TABLE t AS SELECT 1",
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET t.x = s.x",
        "GRANT SELECT ON t TO `user`",
        "TRUNCATE TABLE t",
        "ALTER TABLE t ADD COLUMN x INT",
    ],
)
def test_rejects_write_statements(sql):
    with pytest.raises(SQLValidationError):
        validate_read_only(sql)


def test_allows_forbidden_word_inside_string_literal():
    # The literal value 'DROP' inside a WHERE clause must not trip the keyword scanner.
    validate_read_only("SELECT * FROM t WHERE status = 'DROP'")


def test_ensure_limit_appends_when_missing():
    result = ensure_limit("SELECT * FROM t", default_limit=1000)
    assert "LIMIT 1000" in result


def test_ensure_limit_preserves_existing_limit():
    result = ensure_limit("SELECT * FROM t LIMIT 5", default_limit=1000)
    assert result.count("LIMIT") == 1
    assert "LIMIT 5" in result


def test_validate_and_prepare_sql_end_to_end():
    prepared = validate_and_prepare_sql("SELECT * FROM t", default_limit=1000)
    assert prepared.startswith("SELECT")
    assert "LIMIT 1000" in prepared


def test_validate_identifier_rejects_injection_attempt():
    with pytest.raises(SQLValidationError):
        validate_identifier("t; DROP TABLE x --", "table")


def test_validate_table_ref_accepts_bare_and_qualified():
    assert validate_table_ref("email_events") == "email_events"
    assert validate_table_ref("cat.schema.email_events") == "cat.schema.email_events"


def test_validate_table_ref_rejects_bad_parts():
    with pytest.raises(SQLValidationError):
        validate_table_ref("cat.schema")
