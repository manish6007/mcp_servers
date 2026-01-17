"""
Dynamic LlamaIndex Tools wrapping MCP server tools.
"""

from typing import Any, List, Optional
from llama_index.core.tools import FunctionTool, BaseTool
from mcp_client import get_mcp_client


def create_mcp_tool(name: str, description: str) -> FunctionTool:
    """Create a LlamaIndex tool that wraps an MCP tool call.
    
    Using FunctionTool.from_defaults is more stable than inheriting from BaseTool
    as it handles the LlamaIndex abstract methods automatically.
    """
    async def mcp_tool_wrapper(**kwargs: Any) -> Any:
        """Adapter function to call the MCP tool."""
        client = get_mcp_client()
        try:
            return await client.call_tool(name, kwargs)
        except Exception as e:
            return {"error": str(e), "success": False}

    return FunctionTool.from_defaults(
        async_fn=mcp_tool_wrapper,
        name=name,
        description=description,
    )


async def get_mcp_tools(session: Optional[Any] = None) -> List[BaseTool]:
    """Dynamically discover tools from the MCP server.
    
    Args:
        session: An optional existing ClientSession. If not provided, 
                 one will be established temporarily.
    """
    client = get_mcp_client()
    
    if session:
        # Use existing session if provided (avoids nested TaskGroups)
        result = await session.list_tools()
        return [create_mcp_tool(t.name, t.description) for t in result.tools]
    else:
        # Fallback to creating a new session scope
        async with client.session_scope() as new_session:
            result = await new_session.list_tools()
            return [create_mcp_tool(t.name, t.description) for t in result.tools]
