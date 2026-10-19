"""
database.py
-----------
Handles read-only connection and safe execution against SQLite databases.
Supports both the default college_2 database and user-uploaded SQLite databases.
Enforces read-only mode at both the SQLite URI level and application level.
"""

import os
import sqlite3
from typing import Any, Dict, List, Optional

# Path to the default Spider 1.0 college_2 SQLite database
DB_RELATIVE_PATH = os.path.join("spider_data", "database", "college_2", "college_2.sqlite")
DB_PATH = os.path.abspath(DB_RELATIVE_PATH)


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Opens and returns a connection to an SQLite database strictly in read-only mode.
    Uses SQLite's URI mode '?mode=ro' to ensure SQLite rejects any write attempts.
    
    Args:
        db_path: Optional path to an SQLite database file. Defaults to DB_PATH (college_2.sqlite).
    """
    target_path = os.path.abspath(db_path) if db_path else DB_PATH
    if not os.path.exists(target_path):
        raise FileNotFoundError(f"Database file not found at: {target_path}")

    # Standardize path for SQLite URI (forward slashes)
    formatted_path = target_path.replace("\\", "/")
    uri = f"file:{formatted_path}?mode=ro"

    # sqlite3.connect with uri=True activates read-only enforcement
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def execute_query(sql_query: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a validated SELECT or read-only CTE query against the read-only database.
    
    Args:
        sql_query: A validated SELECT query string.
        db_path: Optional path to the target database. Defaults to DB_PATH.
        
    Returns:
        A dictionary containing:
        - columns: list of column name strings
        - rows: list of tuples/lists representing data rows
        - row_count: integer count of returned rows
    """
    cleaned_sql = sql_query.strip().rstrip(";")

    # Additional application-level defense against non-SELECT / non-CTE queries
    first_token = cleaned_sql.split()[0].upper() if cleaned_sql.split() else ""
    if first_token not in ("SELECT", "WITH"):
        raise ValueError(
            f"Database execution refused: Only SELECT or read-only CTE queries are permitted, got '{first_token}'."
        )

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(cleaned_sql)

        # Extract column names from description
        columns: List[str] = [col[0] for col in cursor.description] if cursor.description else []
        
        # Fetch rows as standard Python lists for JSON serialization
        raw_rows = cursor.fetchall()
        rows: List[List[Any]] = [list(row) for row in raw_rows]

        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        }
    finally:
        conn.close()


def test_connection(db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Tests database accessibility and read-only mode enforcement.
    """
    target_path = os.path.abspath(db_path) if db_path else DB_PATH
    conn = get_db_connection(target_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in cursor.fetchall()]
        return {
            "connected": True,
            "path": target_path,
            "table_count": len(tables),
            "tables": sorted(tables),
        }
    finally:
        conn.close()
