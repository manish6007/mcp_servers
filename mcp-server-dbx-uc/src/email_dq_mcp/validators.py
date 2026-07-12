"""
SQL and identifier validation for read-only, safe Databricks access.

Every SQL string that will be sent to Databricks passes through
`validate_and_prepare_sql` before it is executed. This is the single
security boundary of the server: only SELECT / WITH ... SELECT statements
are allowed, a small set of dangerous statement keywords is rejected, and
a LIMIT clause is guaranteed to be present.
"""

from __future__ import annotations

import re

# Statement-level keywords that must never appear as the leading keyword,
# and are also blocked anywhere a semicolon-separated statement could hide
# them (defense in depth against stacked queries).
FORBIDDEN_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "MERGE",
    "CREATE",
    "GRANT",
    "REVOKE",
    "COPY",
    "REPLACE",
    "OPTIMIZE",
    "VACUUM",
    "SET",
    "USE",
    "CALL",
    "EXEC",
    "EXECUTE",
]

_FORBIDDEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(" + "|".join(FORBIDDEN_KEYWORDS) + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

_LEADING_KEYWORD_PATTERN = re.compile(r"^\s*(WITH|SELECT)\b", re.IGNORECASE)

_LIMIT_PATTERN = re.compile(r"\bLIMIT\s+\d+\s*$", re.IGNORECASE)

# Unity Catalog three-level names: unquoted identifiers made of letters,
# digits, and underscores. Backtick-quoted identifiers are intentionally
# not accepted here to keep the validator simple and unambiguous.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SQLValidationError(ValueError):
    """Raised when a SQL statement fails the read-only safety checks."""


def _strip_sql_comments_and_strings(sql: str) -> str:
    """
    Return a copy of `sql` with string literals and comments blanked out,
    so keyword scanning does not false-positive on text inside a literal
    (e.g. a WHERE clause filtering on the literal string 'CREATE').
    """
    result = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and (j + 1 >= n or sql[j + 1] != "'"):
                    j += 1
                    break
                j += 1
            else:
                j = n
            result.append(" " * (j - i))
            i = j
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            result.append(" " * (j - i))
            i = j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i)
            j = n if j == -1 else j + 2
            result.append(" " * (j - i))
            i = j
        else:
            result.append(ch)
            i += 1
    return "".join(result)


def validate_read_only(sql: str) -> None:
    """Raise SQLValidationError unless `sql` is a single read-only SELECT statement."""
    if not sql or not sql.strip():
        raise SQLValidationError("SQL statement must not be empty.")

    normalized = sql.strip()
    # Allow a single trailing semicolon but reject stacked statements.
    body = normalized[:-1].strip() if normalized.endswith(";") else normalized
    if ";" in body:
        raise SQLValidationError("Multiple statements are not allowed.")

    scrubbed = _strip_sql_comments_and_strings(body)

    if not _LEADING_KEYWORD_PATTERN.match(scrubbed):
        raise SQLValidationError(
            "Only SELECT (or WITH ... SELECT) statements are allowed."
        )

    match = _FORBIDDEN_PATTERN.search(scrubbed)
    if match:
        raise SQLValidationError(
            f"Statement contains a forbidden keyword: {match.group(1).upper()}. "
            "Only read-only SELECT queries are permitted."
        )


def ensure_limit(sql: str, default_limit: int) -> str:
    """Append `LIMIT default_limit` if the statement does not already have one."""
    normalized = sql.strip()
    has_trailing_semicolon = normalized.endswith(";")
    body = normalized[:-1].strip() if has_trailing_semicolon else normalized

    scrubbed = _strip_sql_comments_and_strings(body)
    if _LIMIT_PATTERN.search(scrubbed):
        return body

    return f"{body}\nLIMIT {default_limit}"


def validate_and_prepare_sql(sql: str, default_limit: int) -> str:
    """Validate `sql` is read-only and return it with a LIMIT guaranteed."""
    validate_read_only(sql)
    return ensure_limit(sql, default_limit)


def validate_identifier(value: str, field_name: str) -> str:
    """Validate a catalog/schema/table/column identifier to prevent SQL injection."""
    if not value or not _IDENTIFIER_PATTERN.match(value):
        raise SQLValidationError(
            f"Invalid {field_name}: {value!r}. Must be a valid unquoted identifier "
            "(letters, digits, underscore, not starting with a digit)."
        )
    return value


def validate_identifiers(values: list[str], field_name: str) -> list[str]:
    return [validate_identifier(v, field_name) for v in values]


def qualified_table_name(catalog: str, schema: str, table: str) -> str:
    """Build a validated `catalog.schema.table` reference."""
    validate_identifier(catalog, "catalog")
    validate_identifier(schema, "schema")
    validate_identifier(table, "table")
    return f"{catalog}.{schema}.{table}"


def validate_table_ref(table: str) -> str:
    """
    Validate a table reference that may be either `table` (1-part) or a
    fully qualified `catalog.schema.table` (3-part) name.
    """
    parts = table.split(".")
    if len(parts) not in (1, 3):
        raise SQLValidationError(
            f"Invalid table reference: {table!r}. Use 'table' or 'catalog.schema.table'."
        )
    for part in parts:
        validate_identifier(part, "table reference component")
    return table
