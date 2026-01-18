# Combined MCP Server

A production-grade MCP (Model Context Protocol) server combining **Redshift query capabilities** and **Knowledgebase vector store** features.

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

## Quick Start

### Local Development

1. **Install uv (if not already installed):**
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   # Or on Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. **Start infrastructure:**
   ```bash
   docker-compose up -d postgres localstack
   ```

3. **Install dependencies:**
   ```bash
   uv pip install -e ".[dev]"
   ```

4. **Configure environment:**
   ```bash
   cp .env.example .env.local
   # Edit .env.local with your settings
   ```

5. **Run the server:**
   ```bash
   # With MCP Inspector
   mcp dev src/combined_mcp_server/main.py

   # Or directly
   python -m combined_mcp_server.main
   ```

### ECS Deployment

```bash
# Build container
docker build -t combined-mcp-server .

# Run with health checks
docker run -p 8080:8080 --env-file .env combined-mcp-server
docker run -p 8080:8080 --env-file .env.local -v C:\Users\manis\.aws:/home/appuser/.aws:ro mcp
```

Health endpoints:
- `GET /health` - Liveness probe
- `GET /ready` - Readiness probe
- `GET /status` - Detailed status

## Configuration

See `.env.example` for all configuration options. Key settings:

| Variable | Description |
|----------|-------------|
| `REDSHIFT_CLUSTER_ID` | Redshift cluster identifier |
| `POSTGRES_SECRET_NAME` | Secrets Manager secret for pgvector DB |
| `KNOWLEDGEBASE_S3_BUCKET` | S3 bucket with markdown files |
| `BEDROCK_EMBEDDING_MODEL` | Titan embedding model ID |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Combined MCP Server                 │
├─────────────────────┬───────────────────────────────┤
│   Redshift Tools    │     Knowledgebase Tools       │
│  ─────────────────  │  ───────────────────────────  │
│  • run_query        │  • build_vectorstore          │
│  • list_schemas     │  • query_vectorstore          │
│  • list_tables      │  • get_vectorstore_status     │
│  • describe_table   │                               │
├─────────────────────┴───────────────────────────────┤
│                    Core Services                     │
│  AWS (Secrets Manager, S3, Bedrock, Redshift)       │
│  PostgreSQL + pgvector                              │
└─────────────────────────────────────────────────────┘
```

## Testing

```bash
# Unit tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=combined_mcp_server

# Integration tests (requires Docker)
docker-compose up -d
pytest tests/ -v -m integration
```

## License

MIT
