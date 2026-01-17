"""
Streamlit Chatbot with LlamaIndex + Bedrock + Native MCP Support
"""

import os
import sys
import json
import asyncio
import streamlit as st
import nest_asyncio
from llama_index.core.agent import ReActAgent
from llama_index.core.workflow import Context
from llama_index.llms.bedrock_converse import BedrockConverse

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
    tools = discover_tools_cached()
    st.session_state.discovered_tools = [t.metadata.name for t in tools]

    agent = ReActAgent(
        tools=tools,
        llm=llm,
    )
    
    # Initialize context if needed
    if "workflow_ctx" not in st.session_state:
        st.session_state.workflow_ctx = Context(agent)
    
    return agent, st.session_state.workflow_ctx


async def run_chat(prompt):
    """Run the chat workflow."""
    agent, ctx = get_agent_and_context()
    handler = agent.run(prompt, ctx=ctx)
    response = await handler
    return str(response)


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
        
        # Proactively discover tools to show in sidebar
        tools = discover_tools_cached()
        if tools:
            st.session_state.discovered_tools = [t.metadata.name for t in tools]

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
                        import json
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
                    # Run chat logic via robust runner
                    answer = run_async(run_chat(prompt))
                except Exception as e:
                    import traceback
                    print(f"Agent Error: {traceback.format_exc()}")
                    st.error(f"Error: {e}")
                    answer = f"I'm sorry, I encountered an error: {type(e).__name__}"
                    
                st.markdown(answer)
        
        st.session_state.messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
