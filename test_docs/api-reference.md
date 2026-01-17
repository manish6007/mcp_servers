# API Reference

Complete API reference for the Combined MCP Server.

## Redshift Tools

### run_query

Execute SQL queries on Amazon Redshift with IAM-based authentication.

**Parameters:**
- `sql` (string, required): The SQL query to execute
- `db_user` (string, required): Database user for authentication
- `db_group` (string, optional): Database group for permissions

**Returns:**
- `row_count`: Total number of rows in result
- `columns`: List of column names
- `rows`: Result rows (sample of 20 if > 100 total)
- `is_sample`: Whether result is a sample
- `s3_path`: S3 location of full results (if sampled)
- `execution_time_ms`: Query execution time

**Example:**
```json
{
    "sql": "SELECT customer_id, name FROM customers WHERE region = 'US'",
    "db_user": "analyst",
    "db_group": "sales_team"
}
```

### list_schemas

List all schemas in the Redshift database.

### list_tables

List all tables in a specific schema.

### describe_table

Get detailed column information for a table.

---

## Knowledgebase Tools

### build_vectorstore

Build or rebuild the vector store from S3 markdown files.

**Process:**
1. Downloads all `.md` files from configured S3 bucket
2. Chunks documents into ~1000 character segments
3. Generates embeddings using AWS Bedrock Titan
4. Stores in PostgreSQL with pgvector

**Returns:**
- `status`: Build status (ready, failed)
- `document_count`: Number of document chunks indexed
- `build_time_seconds`: Time taken to build

### query_vectorstore

Search the knowledge base with hybrid search.

**Parameters:**
- `query` (string, required): Search query text
- `top_k` (integer, default: 10): Maximum results to return
- `search_type` (enum, default: "hybrid"): Search mode
  - `semantic`: Vector similarity only
  - `keyword`: Full-text search only
  - `hybrid`: Combined with RRF reranking

**Returns:**
- `results`: List of matching documents
- `search_type`: Type of search performed
- `query_time_ms`: Search duration
- `cached`: Whether result was from cache

### get_vectorstore_status

Get current vector store status and cache statistics.

---

## Error Handling

All tools return a consistent error format:

```json
{
    "success": false,
    "error": "Error message description",
    "error_type": "ErrorClassName"
}
```

## Rate Limits

- Bedrock embeddings: 10 requests/second
- Redshift queries: Limited by cluster capacity
- Query cache: 30 items (FIFO eviction)
