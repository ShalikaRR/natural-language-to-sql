"""
schema.py
---------
Dynamically inspects SQLite databases at runtime (default college_2 or user-uploaded).
Extracts table names, columns, data types, primary keys, and foreign keys,
and formats them for LLM prompt context without hard-coding the schema.
"""

import os
from typing import Any, Dict, List, Optional
from database import get_db_connection, DB_PATH as DEFAULT_DB_PATH

# In-memory caches keyed by database path
_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}
_PROMPT_CACHE: Dict[str, str] = {}


def extract_schema(db_path: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Dynamically queries SQLite metadata for all tables, columns, data types,
    primary keys, and foreign-key relationships for the given database path.
    """
    target_path = os.path.abspath(db_path) if db_path else DEFAULT_DB_PATH

    if target_path in _SCHEMA_CACHE and not force_refresh:
        return _SCHEMA_CACHE[target_path]

    conn = get_db_connection(target_path)
    try:
        cursor = conn.cursor()

        # Query all user tables (exclude sqlite internal tables)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        table_names = [row[0] for row in cursor.fetchall()]

        tables_metadata = {}
        for table in table_names:
            # Query column details: (cid, name, type, notnull, dflt_value, pk)
            cursor.execute(f'PRAGMA table_info("{table}");')
            col_rows = cursor.fetchall()
            columns = []
            primary_keys = []
            for col in col_rows:
                col_name = col[1]
                col_type = col[2] or "TEXT"
                is_pk = bool(col[5])
                if is_pk:
                    primary_keys.append(col_name)
                columns.append({
                    "name": col_name,
                    "type": col_type,
                    "is_primary_key": is_pk
                })

            # Query foreign keys: (id, seq, table, from, to, on_update, on_delete, match)
            cursor.execute(f'PRAGMA foreign_key_list("{table}");')
            fk_rows = cursor.fetchall()
            foreign_keys = []
            for fk in fk_rows:
                foreign_keys.append({
                    "from_column": fk[3],
                    "target_table": fk[2],
                    "target_column": fk[4]
                })

            # Query row count safely
            try:
                cursor.execute(f'SELECT count(*) FROM "{table}";')
                row_count = cursor.fetchone()[0]
            except Exception:
                row_count = 0

            tables_metadata[table] = {
                "columns": columns,
                "primary_keys": primary_keys,
                "foreign_keys": foreign_keys,
                "row_count": row_count
            }

        schema_dict = {
            "database_path": target_path,
            "database_name": os.path.basename(target_path),
            "table_count": len(table_names),
            "tables": tables_metadata
        }
        _SCHEMA_CACHE[target_path] = schema_dict
        return schema_dict
    finally:
        conn.close()


def get_schema_prompt_text(
    db_path: Optional[str] = None,
    db_name: Optional[str] = None,
    force_refresh: bool = False
) -> str:
    """
    Formats the dynamic database schema into a clear, concise text representation
    specifically structured for prompt injection into an LLM.
    """
    target_path = os.path.abspath(db_path) if db_path else DEFAULT_DB_PATH

    if target_path in _PROMPT_CACHE and not force_refresh:
        return _PROMPT_CACHE[target_path]

    schema_data = extract_schema(db_path=target_path, force_refresh=force_refresh)
    effective_name = db_name or os.path.basename(target_path)

    lines: List[str] = [
        f"### Database: {effective_name} (SQLite)",
        "The database contains the following tables and relationships:\n"
    ]

    for table_name, meta in schema_data["tables"].items():
        lines.append(f"Table: {table_name}")
        
        # Format columns with types and primary key indicators
        col_strs = []
        for col in meta["columns"]:
            pk_mark = " [PRIMARY KEY]" if col["is_primary_key"] else ""
            col_strs.append(f"  - {col['name']} ({col['type']}){pk_mark}")
        lines.append("  Columns:\n" + "\n".join(col_strs))

        # Format foreign keys
        if meta["foreign_keys"]:
            fk_strs = []
            for fk in meta["foreign_keys"]:
                fk_strs.append(f"  - {fk['from_column']} -> {fk['target_table']}({fk['target_column']})")
            lines.append("  Foreign Keys:\n" + "\n".join(fk_strs))

        lines.append("")  # Blank line between tables

    prompt_text = "\n".join(lines).strip()
    _PROMPT_CACHE[target_path] = prompt_text
    return prompt_text


def clear_schema_cache(db_path: Optional[str] = None) -> None:
    """Clears cached schema metadata for a specific database or all databases."""
    global _SCHEMA_CACHE, _PROMPT_CACHE
    if db_path:
        target_path = os.path.abspath(db_path)
        _SCHEMA_CACHE.pop(target_path, None)
        _PROMPT_CACHE.pop(target_path, None)
    else:
        _SCHEMA_CACHE.clear()
        _PROMPT_CACHE.clear()
