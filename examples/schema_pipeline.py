import re
import json
import math
from typing import List, Dict

from tone import encode  # <-- TOON encoder

# ============================================================
# 1. MARKDOWN PARSER (TABLE FORMAT)
# ============================================================

def parse_md(md_text: str) -> dict:
    table_match = re.search(r"#\s+(\w+)", md_text)
    if not table_match:
        raise ValueError("Table name not found")
    table = table_match.group(1)

    lines = md_text.splitlines()
    table_lines = []
    in_table = False

    for line in lines:
        line = line.strip()
        if line.startswith("|") and "|" in line[1:]:
            in_table = True
            table_lines.append(line)
        elif in_table:
            break

    if len(table_lines) < 3:
        raise ValueError("Markdown table not found")

    headers = [h.strip() for h in table_lines[0].strip("|").split("|")]
    col_idx = headers.index("column_name")
    desc_idx = headers.index("description")

    columns = []
    descriptions = {}

    for row in table_lines[2:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        col = cells[col_idx]
        desc = cells[desc_idx]
        columns.append(col)
        descriptions[col] = desc

    return {
        "table": table,
        "columns": columns,
        "column_descriptions": descriptions
    }

# ============================================================
# 2. NORMAL JSON SCHEMA (NOT TOON YET)
# ============================================================

def build_schema_json(table: str, columns: List[str]) -> dict:
    return {
        "table": table,
        "columns": columns
    }

# ============================================================
# 3. EMBEDDING TEXT (TITAN-FRIENDLY)
# ============================================================

def build_embedding_text(
    table: str,
    columns: List[str],
    descriptions: Dict[str, str]
) -> str:
    parts = [f"Table {table} stores business data."]
    for col in columns:
        desc = descriptions.get(col, "")[:80]
        parts.append(f"{col} represents {desc}.")
    return " ".join(parts)

# ============================================================
# 4. MOCK TITAN EMBEDDING (POC)
# ============================================================

def embed(text: str) -> List[float]:
    # Mock 1024-dim embedding (deterministic-ish)
    vec = [0.0] * 1024
    for i, w in enumerate(text.split()):
        vec[i % 1024] += (hash(w) % 1000) / 1000
    return vec

# ============================================================
# 5. SIMPLE IN-MEMORY VECTOR STORE
# ============================================================

class InMemoryVectorStore:
    def __init__(self):
        self.records = []

    def insert(self, embedding, metadata):
        self.records.append({
            "embedding": embedding,
            "metadata": metadata
        })

    def similarity_search(self, query_embedding, top_k=2):
        scored = []
        for r in self.records:
            score = cosine_similarity(query_embedding, r["embedding"])
            scored.append((score, r))
        scored.sort(reverse=True, key=lambda x: x[0])
        return [r for _, r in scored[:top_k]]

def cosine_similarity(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(x*x for x in b))
    return dot / (na * nb + 1e-9)

# ============================================================
# 6. BUILD VECTOR STORE (BUILD TIME)
# ============================================================

def build_vectorstore(md_files: List[str]) -> InMemoryVectorStore:
    vs = InMemoryVectorStore()

    for md in md_files:
        parsed = parse_md(md)

        embedding_text = build_embedding_text(
            parsed["table"],
            parsed["columns"],
            parsed["column_descriptions"]
        )

        schema_json = build_schema_json(
            parsed["table"],
            parsed["columns"]
        )

        vs.insert(
            embedding=embed(embedding_text),
            metadata={
                "schema_json": schema_json
            }
        )

    return vs

# ============================================================
# 7. MCP RETRIEVAL TOOL (JSON → TOON HERE)
# ============================================================

def retrieve_from_vectorstore(question: str, vectorstore, top_k=1):
    q_emb = embed(question)
    results = vectorstore.similarity_search(q_emb, top_k=top_k)

    schemas = []
    for r in results:
        schema_json = r["metadata"]["schema_json"]

        # 🔥 Convert JSON → TOON
        toon_schema = encode(schema_json)

        schemas.append(toon_schema)

    return {
        "schemas_toon": schemas
    }

# ============================================================
# 8. RUN POC
# ============================================================

if __name__ == "__main__":
    md_schema = """
# trades

| column_name   | data_type   | description                              |
|--------------|-------------|------------------------------------------|
| trade_id     | int         | Unique identifier of the trade           |
| trade_date   | timestamp   | Date on which the trade was executed     |
| amount       | decimal     | Notional trade value                     |
| counterparty | varchar     | Trading counterparty name                |
"""

    vs = build_vectorstore([md_schema])

    response = retrieve_from_vectorstore(
        "total notional value by counterparty",
        vs
    )

    print("=== TOON OUTPUT (WHAT AGENT SEES) ===")
    print(response["schemas_toon"][0])
