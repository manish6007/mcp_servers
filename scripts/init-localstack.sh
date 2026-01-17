#!/bin/bash
# =============================================================================
# LocalStack Initialization Script
# Creates S3 buckets and Secrets Manager secrets for local development
# =============================================================================

set -e

echo "Initializing LocalStack resources..."

# Wait for LocalStack to be ready
sleep 5

# Create S3 buckets
echo "Creating S3 buckets..."
awslocal s3 mb s3://local-knowledgebase || true
awslocal s3 mb s3://local-results || true

# Upload sample markdown files for testing
echo "Uploading sample knowledge files..."
cat << 'EOF' > /tmp/sample-doc-1.md
# Getting Started Guide

This is a sample document for testing the knowledge base.

## Installation

1. Clone the repository
2. Install dependencies with `pip install -e .`
3. Run the server with `mcp-server`

## Configuration

Set environment variables in `.env` file.
EOF

cat << 'EOF' > /tmp/sample-doc-2.md
# API Reference

## Redshift Tools

### run_query
Execute SQL queries on Redshift cluster.

**Parameters:**
- `sql` (string): The SQL query to execute
- `db_user` (string): Database user for authentication
- `db_group` (string): Database group for permissions

**Returns:**
- `row_count`: Number of rows returned
- `sample_rows`: First 20 rows if result > 100 rows
- `s3_path`: S3 location of full results (if > 100 rows)
EOF

awslocal s3 cp /tmp/sample-doc-1.md s3://local-knowledgebase/docs/getting-started.md
awslocal s3 cp /tmp/sample-doc-2.md s3://local-knowledgebase/docs/api-reference.md

# Create Secrets Manager secret for PostgreSQL credentials
echo "Creating Secrets Manager secrets..."
awslocal secretsmanager create-secret \
    --name my-app/postgres-credentials \
    --secret-string '{"host":"postgres","port":5432,"database":"vectordb","username":"postgres","password":"postgres"}' \
    || awslocal secretsmanager update-secret \
    --secret-id my-app/postgres-credentials \
    --secret-string '{"host":"postgres","port":5432,"database":"vectordb","username":"postgres","password":"postgres"}'

echo "LocalStack initialization complete!"
