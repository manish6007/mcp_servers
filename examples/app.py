"""
Streamlit Chatbot with LlamaIndex + Bedrock + Native MCP Support
"""

import os
import sys
import json
import asyncio
import time
import streamlit as st
import nest_asyncio
from llama_index.core.agent import ReActAgent
from llama_index.core.workflow import Context
from llama_index.llms.bedrock_converse import BedrockConverse
from llama_index.core.tools import ToolOutput

# Standard fix for Streamlit/Windows async loop conflicts
nest_asyncio.apply()

# Fix for Windows: psycopg async and anyio/httpx often require SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from mcp_tools import get_mcp_tools

def run_async(coro):
    """Robustly run an async coroutine using a persistent loop and nest_asyncio."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(coro)

# Page config
st.set_page_config(
    page_title="MCP Knowledge Assistant",
    page_icon="🤖",
    layout="wide",
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: bold;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 2rem;
    }
</style>
""", unsafe_allow_html=True)


def get_llm():
    """Initialize Bedrock LLM using Converse API."""
    region = os.getenv("AWS_REGION", "us-east-1")
    model_id = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
    
    return BedrockConverse(
        model=model_id,
        region_name=region,
        temperature=0.1,
        max_tokens=4096,
    )


@st.cache_resource
def discover_tools_cached():
    """Discover tools once and cache them as resources."""
    try:
        return run_async(get_mcp_tools())
    except Exception as e:
        print(f"Discovery failed: {e}")
        return []


def get_agent_and_context():
    """Initialize workflow ReAct agent and persistent context."""
    llm = get_llm()
    
    # Discovery tools using the cached function
    all_tools = discover_tools_cached()
    
    # Filter out Redshift tools - only keep knowledgebase tools
    redshift_tools = {"run_query", "list_schemas", "list_tables", "describe_table"}
    tools = [t for t in all_tools if t.metadata.name not in redshift_tools]
    
    st.session_state.discovered_tools = [t.metadata.name for t in tools]

    # System prompt to guide tool selection
    system_prompt = """You are a helpful assistant with access to database tools.

IMPORTANT TOOL SELECTION RULES:
1. For Text2SQL or schema lookups: ALWAYS use `query_schemas` first. 
   It returns TOON-encoded schema info (table names, columns, types, descriptions).
2. Do NOT use `query_vectorstore` for schema lookups - it returns raw text, not structured schemas.
3. Do NOT use `list_schemas`, `list_tables`, or `describe_table` (Redshift tools) unless the user explicitly asks about Redshift.

When a user asks about database tables, columns, or wants to write SQL:
1. Call `query_schemas` with the natural language query
2. Use the returned TOON schema to understand table structure
3. Generate SQL based on the schema information
"""

    agent = ReActAgent(
        tools=tools,
        llm=llm,
        system_prompt=system_prompt,
    )
    
    # Initialize context if needed
    if "workflow_ctx" not in st.session_state:
        st.session_state.workflow_ctx = Context(agent)
    
    return agent, st.session_state.workflow_ctx


async def run_chat_with_trace(prompt):
    """Run the chat workflow with full trace capture."""
    agent, ctx = get_agent_and_context()
    
    trace = {
        "tool_calls": [],
        "start_time": time.time(),
    }
    
    # Wrap tools to capture calls
    original_tools = agent.tools
    wrapped_tools = []
    
    for tool in original_tools:
        original_call = tool.call
        original_acall = tool.acall
        tool_name = tool.metadata.name
        
        async def make_traced_acall(t_name, orig_acall):
            async def traced_acall(*args, **kwargs):
                call_start = time.time()
                try:
                    result = await orig_acall(*args, **kwargs)
                    call_time = (time.time() - call_start) * 1000
                    trace["tool_calls"].append({
                        "tool": t_name,
                        "args": kwargs,
                        "result": str(result)[:2000],  # Truncate for display
                        "time_ms": call_time,
                        "success": True,
                    })
                    return result
                except Exception as e:
                    trace["tool_calls"].append({
                        "tool": t_name,
                        "args": kwargs,
                        "error": str(e),
                        "success": False,
                    })
                    raise
            return traced_acall
        
        # Apply tracing
        tool.acall = await make_traced_acall(tool_name, original_acall)
        wrapped_tools.append(tool)
    
    agent.tools = wrapped_tools
    
    handler = agent.run(prompt, ctx=ctx)
    response = await handler
    
    trace["total_time_ms"] = (time.time() - trace["start_time"]) * 1000
    trace["response"] = str(response)
    
    return trace


def main():
    # Header
    st.markdown('<p class="main-header">🤖 MCP Knowledge Assistant</p>', unsafe_allow_html=True)
    
    # Sidebar
    with st.sidebar:
        st.markdown("### ⚙️ Configuration")
        
        mcp_url = st.text_input(
            "MCP Server URL",
            value=os.getenv("MCP_SERVER_URL", "http://localhost:8080"),
        )
        os.environ["MCP_SERVER_URL"] = mcp_url
        
        # Proactively discover tools to show in sidebar (filtered)
        all_tools = discover_tools_cached()
        redshift_tools = {"run_query", "list_schemas", "list_tables", "describe_table"}
        filtered_tools = [t for t in all_tools if t.metadata.name not in redshift_tools]
        if filtered_tools:
            st.session_state.discovered_tools = [t.metadata.name for t in filtered_tools]

        st.markdown("---")
        st.markdown("### 🛠️ Discovered Tools")
        if "discovered_tools" in st.session_state and st.session_state.discovered_tools:
            for tool_name in st.session_state.discovered_tools:
                st.markdown(f"- {tool_name}")
            if st.button("🔄 Refresh Tools"):
                st.cache_resource.clear()
                st.rerun()
        else:
            st.warning("No tools discovered")
            if st.button("🔍 Discover Tools"):
                st.cache_resource.clear()
                st.rerun()
        
        st.markdown("---")
        if st.button("🗑️ Clear Chat"):
            st.session_state.messages = []
            if "workflow_ctx" in st.session_state:
                del st.session_state.workflow_ctx
            st.rerun()
        
        # Check MCP server health
        st.markdown("---")
        st.markdown("### ⚙️ System Actions")
        
        if st.button("🚀 Build Vector Store", help="Download S3 files and rebuild the search index."):
            with st.spinner("Building vector store... this may take a moment."):
                try:
                    # Find the tool in our discovered list
                    tools = discover_tools_cached()
                    build_tool = next((t for t in tools if t.metadata.name == "build_vectorstore"), None)
                    
                    if not build_tool:
                        st.error("Tool 'build_vectorstore' not found in discovered tools.")
                    else:
                        # Call the tool via our robust runner
                        result = run_async(build_tool.acall())
                        
                        # The result of acall is a ToolOutput
                        try:
                            # Try to parse the content as JSON
                            res_data = json.loads(str(result))
                            if not isinstance(res_data, dict):
                                res_data = {"success": True, "message": str(result)}
                        except:
                            res_data = {"success": True, "message": str(result)}

                        if res_data.get("success"):
                            st.success(res_data.get("message", "Build completed!"))
                            st.cache_resource.clear()
                        else:
                            st.error(f"Build failed: {res_data.get('message', 'Unknown error')}")
                except Exception as e:
                    # Unpack ExceptionGroup to show the real error
                    err_msg = str(e)
                    if hasattr(e, "exceptions") and e.exceptions:
                        sub_errs = [str(ex) for ex in e.exceptions]
                        err_msg = f"{err_msg} (Sub-errors: {', '.join(sub_errs)})"
                    
                    st.error(f"Build failed: {err_msg}")
                    import traceback
                    print(traceback.format_exc())

        # Check MCP server health
        import httpx
        try:
            r = httpx.get(f"{mcp_url}/health", timeout=2.0)
            if r.status_code == 200:
                st.success("✅ MCP Server Reachable")
            else:
                st.warning(f"⚠️ MCP Server Status: {r.status_code}")
        except Exception:
            st.error("❌ MCP Server Offline")
    
    # Chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    # Chat input
    if prompt := st.chat_input("Ask about the MCP documentation..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    # Run chat with trace capture
                    trace = run_async(run_chat_with_trace(prompt))
                    answer = trace["response"]
                    elapsed_ms = trace["total_time_ms"]
                    tool_calls = trace.get("tool_calls", [])
                    
                except Exception as e:
                    import traceback
                    print(f"Agent Error: {traceback.format_exc()}")
                    st.error(f"Error: {e}")
                    answer = f"I'm sorry, I encountered an error: {type(e).__name__}"
                    elapsed_ms = 0
                    tool_calls = []
                
                # Show response
                st.markdown(answer)
                
                # Show execution time
                if elapsed_ms > 0:
                    st.caption(f"⏱️ Response time: {elapsed_ms:.0f}ms ({elapsed_ms/1000:.2f}s)")
                
                # Show tool trace in expandable section
                if tool_calls:
                    with st.expander(f"🔧 Agent Trace ({len(tool_calls)} tool calls)", expanded=False):
                        for i, call in enumerate(tool_calls, 1):
                            tool_name = call.get("tool", "unknown")
                            success = call.get("success", False)
                            status_icon = "✅" if success else "❌"
                            call_time = call.get("time_ms", 0)
                            
                            st.markdown(f"**{i}. {status_icon} `{tool_name}`** ({call_time:.0f}ms)")
                            
                            # Show arguments
                            args = call.get("args", {})
                            if args:
                                st.markdown("**Arguments:**")
                                st.code(json.dumps(args, indent=2, default=str), language="json")
                            
                            # Show result or error
                            if success:
                                result = call.get("result", "")
                                st.markdown("**Output:**")
                                # Check if it looks like TOON format
                                if result.startswith("table:") or "columns[" in result:
                                    st.code(result, language="yaml")
                                else:
                                    st.code(result[:1500] + ("..." if len(result) > 1500 else ""), language="json")
                            else:
                                st.error(f"Error: {call.get('error', 'Unknown')}")
                            
                            if i < len(tool_calls):
                                st.markdown("---")
        
        # Store with timing info
        st.session_state.messages.append({
            "role": "assistant", 
            "content": answer,
            "elapsed_ms": elapsed_ms,
            "tool_calls": tool_calls,
        })


if __name__ == "__main__":
    main()
