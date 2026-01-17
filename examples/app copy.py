"""
Streamlit Chatbot with LlamaIndex + Bedrock + MCP Tools

Uses the newer LlamaIndex API with FunctionCallingAgent.
"""

import os
import json
import asyncio
import streamlit as st
from llama_index.core.agent import ReActAgent
from llama_index.core.workflow import Context
from llama_index.llms.bedrock_converse import BedrockConverse

from mcp_tools import get_mcp_tools

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


def get_agent_and_context(discovered_tools=None):
    """Initialize workflow ReAct agent and persistent context."""
    # We create the agent and tools fresh to avoid stale client references,
    # but keep the Context in session_state to maintain conversation memory.
    llm = get_llm()
    
    # Use provided tools or empty list
    tools = discovered_tools or []

    agent = ReActAgent(
        tools=tools,
        llm=llm,
    )
    
    if "workflow_ctx" not in st.session_state:
        st.session_state.workflow_ctx = Context(agent)
        
    return agent, st.session_state.workflow_ctx


async def run_chat_logic(prompt, ctx):
    """Discovery tools + Run agent in a single async scope."""
    from mcp_client import get_mcp_client
    from mcp_tools import get_mcp_tools
    
    client = get_mcp_client()
    
    async with client.session_scope() as session:
        # 1. Discover tools dynamically (passing active session)
        tools = await get_mcp_tools(session=session)
        st.session_state.discovered_tools = [t.metadata.name for t in tools]
        
        # 2. Re-init agent with tools (LlamaIndex agents are lightweight)
        agent, _ = get_agent_and_context(discovered_tools=tools)
        
        # 3. Run the workflow
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
            value=os.getenv("MCP_SERVER_URL", "http://localhost:8000"),
        )
        os.environ["MCP_SERVER_URL"] = mcp_url
        
        st.markdown("---")
        st.markdown("### 🛠️ Discovered Tools")
        if "discovered_tools" in st.session_state and st.session_state.discovered_tools:
            for tool_name in st.session_state.discovered_tools:
                st.markdown(f"- {tool_name}")
        else:
            st.info("Tools will be discovered when you chat")
        
        st.markdown("---")
        if st.button("🔄 Clear Chat"):
            st.session_state.messages = []
            if "workflow_ctx" in st.session_state:
                del st.session_state.workflow_ctx
            if "discovered_tools" in st.session_state:
                del st.session_state.discovered_tools
            st.rerun()
        
        # Check MCP server health
        st.markdown("---")
        import httpx
        try:
            r = httpx.get(f"{mcp_url}/health", timeout=2.0)
            if r.status_code == 200:
                st.success("✅ MCP Server Reachable")
            else:
                st.warning(f"⚠️ MCP Server Status: {r.status_code}")
        except Exception:
            st.error("❌ MCP Server Offline\n\nPlease run the server first!")
    
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
            with st.spinner("Talking to MCP Server..."):
                try:
                    # Initialize context if needed (lazy)
                    if "workflow_ctx" not in st.session_state:
                        # Dummy call to init ctx
                        get_agent_and_context()
                    
                    # Run the single async chat logic
                    answer = asyncio.run(run_chat_logic(prompt, st.session_state.workflow_ctx))
                except Exception as e:
                    import traceback
                    err_msg = str(e)
                    if "TaskGroup" in err_msg:
                        st.error("🔍 **MCP Connection Failed**\n\nThe server might have closed the connection or is unreachable. Check your terminal logs for 'Sub-Exception' details.")
                    else:
                        st.error(f"Error: {e}")
                    print(f"Agent Error: {traceback.format_exc()}")
                    answer = f"I'm sorry, I couldn't connect to the knowledge base server. (Error: {type(e).__name__})"
                    
                st.markdown(answer)
        
        st.session_state.messages.append({"role": "assistant", "content": answer})


if __name__ == "__main__":
    main()
