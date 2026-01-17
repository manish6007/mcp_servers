"""
Native LlamaIndex Tools wrapping MCP server tools.
Uses the official llama-index-tools-mcp package.
"""

import os
from typing import List, Optional
from llama_index.core.tools import BaseTool
from llama_index.tools.mcp import BasicMCPClient, McpToolSpec

# Global instance to avoid session lifecycle issues
_tool_spec: Optional[McpToolSpec] = None
_last_url: Optional[str] = None

def get_tool_spec() -> McpToolSpec:
    """Get or create singleton McpToolSpec, re-initializing if URL changes."""
    global _tool_spec, _last_url
    
    current_url = os.getenv("MCP_SERVER_URL", "http://localhost:8080").rstrip("/")
    mcp_endpoint = f"{current_url}/mcp"
    
    if _tool_spec is None or current_url != _last_url:
        print(f"Initializing MCP Client at {mcp_endpoint}")
        client = BasicMCPClient(mcp_endpoint)
        _tool_spec = McpToolSpec(client=client)
        _last_url = current_url
        
    return _tool_spec

async def get_mcp_tools() -> List[BaseTool]:
    """Dynamically discover tools from the MCP server using native LlamaIndex support."""
    spec = get_tool_spec()
    
    try:
        # Fetching tools from the server and wrapping them as FunctionTools
        # This uses the same long-lived client instance
        tools = await spec.to_tool_list_async()
        print(f"Successfully discovered {len(tools)} tools")
        return tools
    except Exception as e:
        err_msg = str(e)
        # Unpack TaskGroup/ExceptionGroup errors to see the real cause
        if hasattr(e, "exceptions") and e.exceptions:
            sub_errs = [str(ex) for ex in e.exceptions]
            err_msg = f"{err_msg} (Sub-errors: {', '.join(sub_errs)})"
            
        print(f"Error discovering native MCP tools: {err_msg}")
        return []
