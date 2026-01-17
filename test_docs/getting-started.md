# MCP Server Getting Started Guide

Welcome to the Combined MCP Server. This guide will help you get started with both Redshift and Knowledgebase features.

## Installation

1. Clone the repository
2. Install dependencies with `uv pip install -e ".[dev]"`
3. Configure your environment variables in `.env.local`
4. Start the server with `python -m combined_mcp_server.main`

## Quick Start

### Redshift Tools

Use the `run_query` tool to execute SQL queries on your Redshift cluster:

```python
result = await run_query(
    sql="SELECT * FROM users LIMIT 10",
    db_user="analyst",
    db_group="readonly"
)
```

### Knowledgebase Tools

Build your vector store from markdown files:

```python
result = await build_vectorstore()
```

Then query it with hybrid search:

```python
result = await query_vectorstore(
    query="How do I configure the server?",
    top_k=5,
    search_type="hybrid"
)
```

## Configuration

All configuration is done through environment variables. See `.env.example` for available options.

### Required Variables

- `REDSHIFT_CLUSTER_ID` - Your Redshift cluster identifier
- `POSTGRES_SECRET_NAME` - Secrets Manager secret for PostgreSQL
- `KNOWLEDGEBASE_S3_BUCKET` - S3 bucket with markdown files

## Support

For issues or questions, please open an issue on GitHub.
