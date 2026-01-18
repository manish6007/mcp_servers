# Combined MCP Server

A production-grade MCP (Model Context Protocol) server combining **Redshift query capabilities** and **Knowledgebase vector store** features with **TOON schema encoding** for Text2SQL agents.

## Features

### Redshift Tools
- **run_query** - Execute SQL with IAM authentication via `get_cluster_credentials`
- **list_schemas** - List database schemas
- **list_tables** - List tables in a schema
- **describe_table** - Get table structure

Large results (>100 rows) are automatically stored in S3 with 20 sample rows returned.

### Knowledgebase Tools
- **build_vectorstore** - Build vector store from S3 markdown files
- **query_vectorstore** - Hybrid search (semantic + keyword) with RRF reranking
- **get_vectorstore_status** - Check build status and cache stats
- **query_schemas** - 🆕 TOON-encoded schema retrieval for Text2SQL agents

### TOON Schema Encoding

TOON (Token-Optimized Object Notation) reduces token usage by ~40-50% compared to JSON.

**Example TOON output:**
```
table: trade
columns[13]{name,type,description}:
  trade_id,varchar,Unique identifier for the trade
  trade_date,timestamp,Date and time when the trade was executed
```

**Markdown schema format:**
```markdown
# trade

| column_name | data_type | description |
|-------------|-----------|-------------|
| trade_id    | varchar   | Unique identifier |
```

## Quick Start

### Local Development

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Start infrastructure
docker-compose up -d postgres localstack

# Install dependencies
uv pip install -e ".[dev]"

# Configure
cp .env.example .env.local

# Run server
mcp dev src/combined_mcp_server/main.py
```

### Docker Deployment

```bash
docker build -t mcp .
docker run -p 8080:8080 --env-file .env.local -v ~/.aws:/home/appuser/.aws:ro mcp
```

Health endpoints: `/health`, `/ready`, `/status`

### Streamlit Chat App

```bash
cd examples
streamlit run app.py
```

Features:
- LlamaIndex ReAct agent with Bedrock Claude
- Dynamic MCP tool discovery
- Agent trace UI showing tool calls and outputs

## Configuration

| Variable | Description |
|----------|-------------|
| `REDSHIFT_CLUSTER_ID` | Redshift cluster identifier |
| `POSTGRES_SECRET_NAME` | Secrets Manager secret for pgvector |
| `KNOWLEDGEBASE_S3_BUCKET` | S3 bucket with markdown files |
| `BEDROCK_EMBEDDING_MODEL` | Titan embedding model ID |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Combined MCP Server                 │
├─────────────────────┬───────────────────────────────┤
│   Redshift Tools    │     Knowledgebase Tools       │
│  • run_query        │  • build_vectorstore          │
│  • list_schemas     │  • query_vectorstore          │
│  • list_tables      │  • get_vectorstore_status     │
│  • describe_table   │  • query_schemas (TOON)       │
├─────────────────────┴───────────────────────────────┤
│  AWS (S3, Bedrock, Redshift) │ PostgreSQL+pgvector  │
└─────────────────────────────────────────────────────┘
```

## Database Schema

```sql
ALTER TABLE knowledgebase.documents ADD COLUMN schema_toon TEXT;
```

## Testing

```bash
pytest tests/ -v --cov=combined_mcp_server
```

## License

MIT
