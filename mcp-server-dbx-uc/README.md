# email-dq-mcp

An MCP server that turns Claude into a **Data Quality Investigator** for an
Email Analytics platform running on **Databricks Unity Catalog**.

This is deliberately *not* a generic Text2SQL assistant. It exposes a small
set of investigation-oriented tools -- bounded read-only SQL, standardized
data quality checks (row counts, nulls, duplicates, freshness, metric
drift), failed-record retrieval, and a one-shot investigation report -- so
Claude can reason over real evidence pulled from Databricks and produce a
root-cause analysis, instead of writing ad-hoc SQL from scratch every time.

The server has no long-running process: it starts when an MCP client
(Claude Desktop, Cursor, `uvx`) invokes it over stdio, and exits when the
client disconnects.

## Features

- **Read-only by construction.** Every SQL statement, whether typed by
  Claude or built internally by a check, passes through a single validator:
  only `SELECT` / `WITH ... SELECT` is allowed; `INSERT`, `UPDATE`,
  `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `MERGE`, `CREATE`, `GRANT`, and
  `REVOKE` are rejected. A `LIMIT 1000` is appended automatically when
  missing. Query timeout is capped at 30 seconds.
- **Metadata-driven DQ rules.** `src/email_dq_mcp/dq_rules.py` maps each
  table to its business keys, freshness column, critical columns, and
  anomaly thresholds. Onboarding a new dataset is a metadata edit, not a
  code change.
- **Structured, Claude-friendly JSON output.** Every tool returns a typed
  result (status, severity, evidence) so Claude can reason over it directly
  instead of parsing free text.
- **PAT never logged.** Logging captures tool name, duration, and statement
  status only -- never full result sets, never the token.

## Tools

| Tool | Purpose |
|---|---|
| `execute_sql` | Run an arbitrary read-only SELECT and get JSON rows back. |
| `get_schema` | Column name / type / nullability / ordinal position for a table. |
| `get_sample_data` | A handful of sample rows (default 20, max 100). |
| `compare_row_counts` | Row count on two dates, with delta and % change. |
| `detect_null_anomalies` | Per-column null % vs. historical average, with anomaly flags. |
| `detect_duplicates` | Duplicate groups for a set of business keys on a date. |
| `detect_freshness_issues` | How stale is the latest record vs. a threshold. |
| `compare_metrics` | A numeric metric across two dates, broken down by a group, with top movers. |
| `get_failed_records` | Rows matching an arbitrary DQ failure condition. |
| `run_email_analytics_dq` | **Primary tool.** Runs the full standard suite and returns a consolidated investigation report. |

## Installation

### Requirements

- Python 3.12+
- A Databricks SQL Warehouse and a Personal Access Token with `SELECT`
  privileges on the tables you want to investigate.
- [`uv`](https://docs.astral.sh/uv/) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

### Local development

```bash
cd mcp-server-dbx-uc
uv sync --extra dev
cp .env.example .env
# edit .env with your DATABRICKS_HOST / DATABRICKS_PAT / DATABRICKS_WAREHOUSE_ID

uv run pytest

# Run the server directly against stdio (for manual testing / mcp dev):
uv run email-dq-mcp
```

### Running via `uvx`

Once published (or run directly from a local checkout / git ref), the
server is designed to be launched on demand -- no persistent process to
manage:

```bash
uvx email-dq-mcp
```

From a local checkout without publishing to PyPI:

```bash
uvx --from /path/to/mcp-server-dbx-uc email-dq-mcp
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABRICKS_HOST` | yes | Workspace URL, e.g. `https://your-workspace.cloud.databricks.com`. |
| `DATABRICKS_PAT` | yes | Personal Access Token. Never logged, never returned in tool output. |
| `DATABRICKS_WAREHOUSE_ID` | yes | SQL Warehouse ID used for statement execution. |
| `QUERY_TIMEOUT_SECONDS` | no | Default/max statement timeout (default `30`, capped at `30`). |
| `DEFAULT_ROW_LIMIT` | no | LIMIT auto-appended to unbounded SELECTs (default `1000`). |
| `MAX_SAMPLE_ROWS` | no | Ceiling for `get_sample_data` (default `100`). |
| `HISTORICAL_LOOKBACK_DAYS` | no | Window used for null-anomaly historical averages (default `7`). |
| `LOG_LEVEL` | no | Python log level (default `INFO`). |

## Claude Code configuration

Claude Code (the CLI) manages MCP servers via `claude mcp add` or a
project-level `.mcp.json`. Either approach gives Claude Code access to
`execute_sql`, `get_schema`, `get_sample_data`, and the DQ investigation
tools against your Unity Catalog SQL warehouse.

**Option A -- CLI (recommended for local use):**

Flags (`--env`, `--transport`, `--scope`) must come *before* the server
name; everything after `--` is passed straight through to the server
command untouched:

```bash
claude mcp add \
  --env DATABRICKS_HOST=https://your-workspace.cloud.databricks.com \
  --env DATABRICKS_PAT=dapi_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  --env DATABRICKS_WAREHOUSE_ID=0123456789abcdef \
  --transport stdio \
  email-dq-mcp \
  -- uvx email-dq-mcp
```

Add `--scope user` if you want the server available across all your
projects instead of just the current repo (default scope is local /
project-specific).

**Option B -- project-level `.mcp.json`** (checked into the repo, secrets
supplied via shell env instead of hardcoded):

```json
{
  "mcpServers": {
    "email-dq-mcp": {
      "command": "uvx",
      "args": ["email-dq-mcp"],
      "env": {
        "DATABRICKS_HOST": "${DATABRICKS_HOST}",
        "DATABRICKS_PAT": "${DATABRICKS_PAT}",
        "DATABRICKS_WAREHOUSE_ID": "${DATABRICKS_WAREHOUSE_ID}"
      }
    }
  }
}
```

With `.mcp.json`, export `DATABRICKS_HOST` / `DATABRICKS_PAT` /
`DATABRICKS_WAREHOUSE_ID` in your shell (or a `.env` loaded by your shell
profile) before launching `claude` -- this keeps the PAT out of version
control.

For a local checkout instead of the published package, swap the command for:

```bash
claude mcp add \
  --env DATABRICKS_HOST=https://your-workspace.cloud.databricks.com \
  --env DATABRICKS_PAT=dapi_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
  --env DATABRICKS_WAREHOUSE_ID=0123456789abcdef \
  --transport stdio \
  email-dq-mcp \
  -- uvx --from /absolute/path/to/mcp-server-dbx-uc email-dq-mcp
```

Verify it's connected with `claude mcp list`, then ask Claude Code
something like *"list the columns in main.email_analytics.email_events"*
or *"run a SELECT COUNT(*) on main.email_analytics.email_events for
2026-07-11"* to confirm it can reach the warehouse.

## Claude Desktop configuration

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "email-dq-mcp": {
      "command": "uvx",
      "args": ["email-dq-mcp"],
      "env": {
        "DATABRICKS_HOST": "https://your-workspace.cloud.databricks.com",
        "DATABRICKS_PAT": "dapi_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "DATABRICKS_WAREHOUSE_ID": "0123456789abcdef"
      }
    }
  }
}
```

For a local checkout instead of a published package:

```json
{
  "mcpServers": {
    "email-dq-mcp": {
      "command": "uvx",
      "args": ["--from", "/absolute/path/to/mcp-server-dbx-uc", "email-dq-mcp"],
      "env": {
        "DATABRICKS_HOST": "https://your-workspace.cloud.databricks.com",
        "DATABRICKS_PAT": "dapi_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "DATABRICKS_WAREHOUSE_ID": "0123456789abcdef"
      }
    }
  }
}
```

## Cursor configuration

Add to `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "email-dq-mcp": {
      "command": "uvx",
      "args": ["email-dq-mcp"],
      "env": {
        "DATABRICKS_HOST": "https://your-workspace.cloud.databricks.com",
        "DATABRICKS_PAT": "dapi_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "DATABRICKS_WAREHOUSE_ID": "0123456789abcdef"
      }
    }
  }
}
```

## Adding a new dataset

Edit `src/email_dq_mcp/dq_rules.py`:

```python
DQ_RULES: dict[str, TableRules] = {
    "email_events": {
        "business_keys": ["email_id", "event_type", "event_timestamp"],
        "date_column": "business_date",
        "freshness_column": "event_timestamp",
        "freshness_threshold_hours": 6.0,
        "critical_columns": ["email_id", "campaign_id", "recipient_id"],
        "null_anomaly_threshold_pct": 5.0,
        "row_count_drop_warning_pct": 10.0,
        "row_count_drop_critical_pct": 30.0,
        "default_metric_column": "email_id",
        "default_group_by_column": "campaign_id",
    },
    # add a new table here -- no code changes required
}
```

Tables without an entry still work: `run_email_analytics_dq` and friends
fall back to sane defaults (`date_column="business_date"`, generic anomaly
thresholds), but checks are more precise once a table is registered.

## Example prompts

> Analyze email analytics data quality for business date 2026-07-11.
> Use all available MCP tools. Investigate: row count drops, duplicate
> records, null spikes, freshness issues, campaign-level anomalies, and
> metric deviations from previous day.
>
> Provide: (1) an executive summary, (2) severity assessment, (3) root
> cause hypotheses, (4) evidence supporting each hypothesis, and
> (5) recommended remediation actions.

> Run `run_email_analytics_dq` for `main.email_analytics.email_events` on
> 2026-07-11, then use `get_failed_records` to pull the actual rows behind
> the worst finding and tell me what's most likely broken upstream.

> Compare `opens` for `main.email_analytics.campaign_performance` between
> 2026-07-10 and 2026-07-11, grouped by `campaign_id`, and tell me which
> campaigns are driving the change.

## Security model

1. `validators.py` is the single choke point every SQL statement passes
   through -- both user-supplied SQL (`execute_sql`) and SQL built
   internally by the DQ checks.
2. Catalog/schema/table/column identifiers supplied by tool callers are
   validated against a strict identifier pattern before being interpolated
   into SQL; they are never passed through as raw string concatenation.
3. Only one statement per call is allowed (no `;`-stacked statements).
4. A `LIMIT` is always enforced, and statement execution is capped at 30
   seconds server-side, independent of what the caller requests.
5. The PAT is read once from the environment, used only in the
   `Authorization` header, and a logging filter redacts it if it were ever
   to appear in a log message.

## Testing

```bash
uv run pytest
```

Tests cover the SQL validator (read-only enforcement, LIMIT injection,
identifier validation) and the DQ rules metadata lookup. They do not hit a
real Databricks workspace.

## Project layout

```
mcp-server-dbx-uc/
  src/email_dq_mcp/
    server.py             # FastMCP app; all 10 tools
    databricks_client.py  # Statement Execution API client (submit/poll/fetch, retries)
    dq_rules.py            # metadata-driven DQ rule framework
    validators.py          # read-only SQL + identifier validation
    models.py               # Pydantic response models
    config.py                # env/config loading + logging (PAT redaction)
  tests/
  pyproject.toml
  .env.example
```
