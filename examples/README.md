# MCP LlamaIndex Chatbot Example

A simple Streamlit chatbot that uses LlamaIndex with AWS Bedrock, connecting to the Combined MCP Server in HTTP mode.

## Prerequisites

1. Combined MCP Server running in HTTP mode:
   ```powershell
   $env:MCP_TRANSPORT="http"
   python -m combined_mcp_server.main
   ```

2. AWS credentials configured with Bedrock access

## Installation

```bash
cd examples
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

## Features

- Chat interface with AWS Bedrock Claude
- MCP tool integration via HTTP/SSE transport
- Knowledge base search using MCP tools
- Conversation history

## Configuration

Set these environment variables:
- `AWS_REGION` - AWS region (default: us-east-1)
- `BEDROCK_MODEL_ID` - Bedrock model (default: anthropic.claude-3-sonnet-20240229-v1:0)
- `MCP_SERVER_URL` - MCP server URL (default: http://localhost:8000)
