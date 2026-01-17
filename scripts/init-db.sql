-- =============================================================================
-- PostgreSQL Initialization Script
-- Creates pgvector extension and knowledge base tables
-- =============================================================================

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable full-text search (pg_trgm for fuzzy matching)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Create schema for knowledgebase
CREATE SCHEMA IF NOT EXISTS knowledgebase;

-- Knowledge base documents table
CREATE TABLE IF NOT EXISTS knowledgebase.documents (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    embedding vector(1024),  -- Titan embed v2 dimension
    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    source_path TEXT,
    chunk_index INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create HNSW index for fast vector similarity search
CREATE INDEX IF NOT EXISTS idx_documents_embedding_hnsw 
ON knowledgebase.documents 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Create GIN index for full-text search
CREATE INDEX IF NOT EXISTS idx_documents_fts 
ON knowledgebase.documents 
USING GIN (fts);

-- Create index on metadata for filtering
CREATE INDEX IF NOT EXISTS idx_documents_metadata 
ON knowledgebase.documents 
USING GIN (metadata);

-- Create index on source_path for deduplication
CREATE INDEX IF NOT EXISTS idx_documents_source_path 
ON knowledgebase.documents (source_path);

-- Build status table for tracking vectorstore state
CREATE TABLE IF NOT EXISTS knowledgebase.build_status (
    id SERIAL PRIMARY KEY,
    status VARCHAR(50) NOT NULL,  -- 'building', 'ready', 'failed'
    document_count INTEGER DEFAULT 0,
    last_build_started_at TIMESTAMP WITH TIME ZONE,
    last_build_completed_at TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    metadata JSONB DEFAULT '{}'
);

-- Insert initial status
INSERT INTO knowledgebase.build_status (status, document_count)
VALUES ('pending', 0)
ON CONFLICT DO NOTHING;

-- Function to update document timestamp
CREATE OR REPLACE FUNCTION knowledgebase.update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-update timestamps
DROP TRIGGER IF EXISTS trigger_update_documents_timestamp ON knowledgebase.documents;
CREATE TRIGGER trigger_update_documents_timestamp
    BEFORE UPDATE ON knowledgebase.documents
    FOR EACH ROW
    EXECUTE FUNCTION knowledgebase.update_updated_at();

-- Grant permissions (adjust as needed for your setup)
-- GRANT ALL PRIVILEGES ON SCHEMA knowledgebase TO your_app_user;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA knowledgebase TO your_app_user;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA knowledgebase TO your_app_user;
