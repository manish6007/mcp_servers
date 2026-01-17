# Troubleshooting Guide

Common issues and solutions for the Combined MCP Server.

## Connection Issues

### PostgreSQL Connection Failed

**Symptom:** `PostgresConnectionError: Failed to connect`

**Solutions:**
1. Verify PostgreSQL is running on the configured host/port
2. Check credentials in environment variables or Secrets Manager
3. Ensure the `vectordb` database exists
4. Verify pgvector extension is installed

```sql
-- Check if pgvector is installed
SELECT * FROM pg_extension WHERE extname = 'vector';

-- Install if missing
CREATE EXTENSION IF NOT EXISTS vector;
```

### Redshift Authentication Failed

**Symptom:** `RedshiftConnectionError: Failed to get credentials`

**Solutions:**
1. Verify IAM permissions for `redshift:GetClusterCredentials`
2. Check cluster identifier is correct
3. Ensure the db_user has appropriate permissions

---

## Vector Store Issues

### Build Failed

**Symptom:** Vector store build fails or times out

**Solutions:**
1. Check S3 bucket permissions
2. Verify Bedrock access for embedding model
3. Reduce batch size for large document sets
4. Check PostgreSQL disk space

### Search Returns No Results

**Symptom:** `query_vectorstore` returns empty results

**Solutions:**
1. Verify vector store is built (`get_vectorstore_status`)
2. Check if documents were loaded: `document_count > 0`
3. Try different search types (semantic vs keyword)
4. Verify query embeddings are being generated

---

## Performance Issues

### Slow Query Response

**Symptom:** Queries take several seconds

**Solutions:**
1. Check if result is cached (should be faster on repeat)
2. Reduce `top_k` parameter
3. Ensure HNSW index exists on embedding column
4. Consider upgrading PostgreSQL resources

### High Memory Usage

**Symptom:** Server consumes excessive memory

**Solutions:**
1. Reduce `QUERY_CACHE_SIZE` environment variable
2. Use smaller embedding dimension if possible
3. Implement document chunking for large files

---

## Health Check Failures

### Readiness Probe Failing

**Symptom:** `/ready` returns 503

**Causes:**
1. Vector store not yet initialized
2. PostgreSQL connection not established
3. Build process still running

**Check status:**
```bash
curl http://localhost:8080/status
```

---

## Logging

Enable debug logging for more information:

```bash
export LOG_LEVEL=DEBUG
export LOG_FORMAT=console
```

Logs include:
- Connection attempts and results
- Query execution times
- Cache hits/misses
- S3 operations
