"""
Pytest fixtures for testing.
"""

import os
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def set_test_environment():
    """Set test environment variables."""
    env_vars = {
        "AWS_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "REDSHIFT_CLUSTER_ID": "test-cluster",
        "REDSHIFT_DATABASE": "testdb",
        "REDSHIFT_HOST": "localhost",
        "REDSHIFT_PORT": "5439",
        "REDSHIFT_RESULTS_BUCKET": "test-results",
        "KNOWLEDGEBASE_S3_BUCKET": "test-kb",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DATABASE": "testdb",
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": "postgres",
        "VECTORSTORE_TABLE_NAME": "test_documents",
        "VECTORSTORE_EMBEDDING_DIMENSION": "1024",
        "LOG_LEVEL": "DEBUG",
        "LOG_FORMAT": "console",
    }
    
    with patch.dict(os.environ, env_vars):
        # Clear cached settings
        from combined_mcp_server.config import get_settings
        get_settings.cache_clear()
        yield


@pytest.fixture
def mock_boto3_client():
    """Mock boto3 client for AWS services."""
    with patch("boto3.client") as mock_client:
        yield mock_client


@pytest.fixture
def mock_s3_client():
    """Mock S3 client."""
    mock = MagicMock()
    mock.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "docs/test.md", "Size": 100, "LastModified": "2024-01-01T00:00:00Z"}
        ]
    }
    mock.get_object.return_value = {
        "Body": MagicMock(read=lambda: b"# Test Document\n\nThis is test content.")
    }
    return mock


@pytest.fixture
def mock_bedrock_client():
    """Mock Bedrock runtime client."""
    import json
    
    mock = MagicMock()
    mock.invoke_model.return_value = {
        "body": MagicMock(
            read=lambda: json.dumps({"embedding": [0.1] * 1024}).encode()
        )
    }
    return mock


@pytest.fixture
def sample_documents():
    """Sample documents for testing."""
    return [
        {
            "id": 1,
            "content": "This is the first test document about Python programming.",
            "metadata": {"title": "Python Guide", "source_file": "python.md"},
            "score": 0.95,
        },
        {
            "id": 2,
            "content": "This is about database management with PostgreSQL.",
            "metadata": {"title": "PostgreSQL Tutorial", "source_file": "postgres.md"},
            "score": 0.85,
        },
        {
            "id": 3,
            "content": "Machine learning basics and best practices.",
            "metadata": {"title": "ML Guide", "source_file": "ml.md"},
            "score": 0.75,
        },
    ]
