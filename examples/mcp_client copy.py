"""
MCP Client for Streamable HTTP transport.

Uses the official MCP Python SDK for Streamable HTTP.
"""

import asyncio
import os
import contextlib
from typing import Any
from contextvars import ContextVar

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

# ContextVar to hold the session for the current task/loop
_current_session: ContextVar[ClientSession] = ContextVar("mcp_session")


class MCPClient:
    """Client for FastMCP server over Streamable HTTP transport."""

    def __init__(self, base_url: str | None = None):
        """Initialize MCP client.
        
        Args:
            base_url: Base URL of the MCP server
        """
        self.base_url = (base_url or os.getenv("MCP_SERVER_URL", "http://localhost:8000")).rstrip("/")
        self.mcp_endpoint = f"{self.base_url}/mcp"

    @contextlib.asynccontextmanager
    async def session_scope(self):
        """Establish a session and manage its lifecycle for the current scope.
        
        This manages the streamable_http_client context which is sensitive to 
        asyncio task/loop changes (common in Streamlit).
        """
        async with contextlib.AsyncExitStack() as stack:
            try:
                # 1. Establish the HTTP/SSE streams
                streams = await stack.enter_async_context(
                    streamable_http_client(self.mcp_endpoint)
                )
                read_stream, write_stream, _ = streams
                
                # 2. Setup the MCP session
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                
                await session.initialize()
                
                # 3. Store session in ContextVar for tools to find
                token = _current_session.set(session)
                try:
                    yield session
                finally:
                    _current_session.reset(token)
            except Exception as e:
                # In Python 3.11+, TaskGroup errors are often ExceptionGroups
                if hasattr(e, "exceptions") and e.exceptions:
                    for i, ex in enumerate(e.exceptions):
                        print(f"MCP Session Sub-Exception {i+1}: {ex}")
                raise e

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the MCP server using the current active session."""
        try:
            session = _current_session.get()
        except LookupError:
            # Fallback for simple calls without explicit scope, 
            # though scope is preferred for efficiency.
            async with self.session_scope() as session:
                return await self._do_call(session, name, arguments)
        
        return await self._do_call(session, name, arguments)

    async def _do_call(self, session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Internal helper to execute the tool call."""
        result = await session.call_tool(name, arguments)
        
        # Parse the result content
        if hasattr(result, "content"):
            for item in result.content:
                if item.type == "text":
                    import json
                    try:
                        return json.loads(item.text)
                    except json.JSONDecodeError:
                        return {"result": item.text}
        
        return {"result": str(result)}


def get_mcp_client(base_url: str | None = None) -> MCPClient:
    """Get MCP client instance."""
    return MCPClient(base_url)
