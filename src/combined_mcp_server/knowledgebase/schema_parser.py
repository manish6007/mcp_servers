"""
Schema parser for markdown-formatted database schema files.

Parses markdown tables containing column definitions and converts
to structured formats for embedding and TOON encoding.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from combined_mcp_server.utils.logging import get_logger

logger = get_logger(__name__)

# Try to import toneformat, fallback to custom encoder
try:
    from tone import encode as toon_encode
    logger.debug("Using toneformat library for TOON encoding")
except ImportError:
    logger.warning("toneformat not available, using custom encoder")
    
    def toon_encode(data: dict) -> str:
        """
        Fallback TOON encoder when toneformat is not installed.
        """
        lines = []
        
        if "table" in data:
            lines.append(f"table: {data['table']}")
        
        if "columns" in data:
            cols = data["columns"]
            if cols and isinstance(cols[0], dict):
                keys = list(cols[0].keys())
                lines.append(f"columns[{len(cols)}]{{{','.join(keys)}}}:")
                for col in cols:
                    values = [str(col.get(k, "")).replace(",", ";") for k in keys]
                    lines.append(f"  {','.join(values)}")
            else:
                lines.append(f"columns: {','.join(cols)}")
        
        return "\n".join(lines)


@dataclass
class SchemaColumn:
    """A column in a database schema."""
    name: str
    data_type: str = ""
    description: str = ""


@dataclass
class TableSchema:
    """Parsed table schema from markdown."""
    table_name: str
    columns: list[SchemaColumn] = field(default_factory=list)
    raw_content: str = ""


def parse_schema_markdown(md_text: str) -> TableSchema | None:
    """
    Parse a markdown schema file into structured TableSchema.
    
    Expected format:
    ```
    # table_name
    
    | column_name | data_type | description |
    |-------------|-----------|-------------|
    | id          | int       | Primary key |
    ```
    
    Args:
        md_text: Markdown content containing table schema
        
    Returns:
        TableSchema if parsing successful, None otherwise
    """
    # Extract table name from first heading
    table_match = re.search(r"^#\s+(\w+)", md_text, re.MULTILINE)
    if not table_match:
        logger.warning("No table name found in markdown")
        return None
    
    table_name = table_match.group(1)
    
    # Find markdown table
    lines = md_text.splitlines()
    table_lines = []
    in_table = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and "|" in stripped[1:]:
            in_table = True
            table_lines.append(stripped)
        elif in_table and not stripped.startswith("|"):
            break  # End of table
    
    if len(table_lines) < 3:  # Header, separator, at least one row
        logger.warning("Markdown table not found or too short", table=table_name)
        return None
    
    # Parse header row
    headers = [h.strip().lower() for h in table_lines[0].strip("|").split("|")]
    
    # Find column indices
    col_name_idx = headers.index("column_name") if "column_name" in headers else None
    data_type_idx = headers.index("data_type") if "data_type" in headers else None
    desc_idx = headers.index("description") if "description" in headers else None
    
    if col_name_idx is None:
        logger.warning("column_name header not found", table=table_name)
        return None
    
    # Parse data rows (skip header and separator)
    columns = []
    for row in table_lines[2:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) > col_name_idx:
            col = SchemaColumn(
                name=cells[col_name_idx],
                data_type=cells[data_type_idx] if data_type_idx and len(cells) > data_type_idx else "",
                description=cells[desc_idx] if desc_idx and len(cells) > desc_idx else "",
            )
            columns.append(col)
    
    logger.debug(
        "Parsed schema",
        table=table_name,
        column_count=len(columns),
    )
    
    return TableSchema(
        table_name=table_name,
        columns=columns,
        raw_content=md_text,
    )


def build_embedding_text(schema: TableSchema) -> str:
    """
    Build embedding-friendly text from schema.
    
    Creates a natural language description suitable for
    Amazon Titan embedding model.
    
    Args:
        schema: Parsed table schema
        
    Returns:
        Text optimized for embedding
    """
    parts = [f"Table {schema.table_name} stores business data."]
    
    for col in schema.columns:
        desc = col.description[:80] if col.description else col.name
        parts.append(f"{col.name} represents {desc}.")
    
    return " ".join(parts)


def build_schema_json(schema: TableSchema) -> dict[str, Any]:
    """
    Build JSON schema representation.
    
    Args:
        schema: Parsed table schema
        
    Returns:
        JSON-serializable schema dict
    """
    return {
        "table": schema.table_name,
        "columns": [
            {
                "name": col.name,
                "type": col.data_type,
                "description": col.description,
            }
            for col in schema.columns
        ],
    }


def build_schema_toon(schema: TableSchema) -> str:
    """
    Build TOON-encoded schema for agent context.
    
    Converts schema to compact TOON format for efficient
    token usage in LLM context.
    
    Args:
        schema: Parsed table schema
        
    Returns:
        TOON-encoded string
    """
    schema_json = build_schema_json(schema)
    return toon_encode(schema_json)


def is_schema_markdown(content: str) -> bool:
    """
    Check if content appears to be a schema markdown file.
    
    Args:
        content: Markdown content
        
    Returns:
        True if content has schema table structure
    """
    # Look for table heading and column_name in header
    has_heading = bool(re.search(r"^#\s+\w+", content, re.MULTILINE))
    has_column_header = "column_name" in content.lower()
    has_table = bool(re.search(r"\|.*\|.*\|", content))
    
    return has_heading and has_column_header and has_table
