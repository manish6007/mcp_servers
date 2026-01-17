import asyncio
import os
import sys
import traceback

# Fix for Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from combined_mcp_server.knowledgebase.tools import build_vectorstore

async def run_build():
    print("Triggering build_vectorstore...")
    try:
        result = await build_vectorstore()
        print(f"BUILD RESULT: {result}")
    except Exception as e:
        print(f"BUILD CRASHED: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_build())
