import asyncio
import os
import sys
import traceback

# Fix for Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from combined_mcp_server.knowledgebase.pg_connection import get_postgres_connection_manager
from combined_mcp_server.config import get_settings

async def check_status():
    print("Loading settings...")
    try:
        settings = get_settings()
        print(f"Postgres Host: {settings.postgres.host}")
        print(f"Postgres Database: {settings.postgres.database}")
        print(f"Postgres User: {settings.postgres.user}")
    except Exception as e:
        print(f"Error loading settings: {e}")
        return

    print("Connecting to database...")
    db = get_postgres_connection_manager()
    try:
        # Use execute (sync) to avoid any async loop issues for now
        query = "SELECT status, document_count, last_error FROM knowledgebase.build_status WHERE id = 1"
        result = db.execute(query)
        if result:
            row = result[0]
            print(f"DATABASE STATUS:")
            print(f"  Status: {row['status']}")
            print(f"  Document Count: {row['document_count']}")
            print(f"  Last Error: {row['last_error']}")
        else:
            print("No build status found in database (id=1).")
    except Exception as e:
        print(f"FAILED TO QUERY DB: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    # Using run() is fine since we apply the policy at top level
    asyncio.run(check_status())
