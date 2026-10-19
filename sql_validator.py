"""
sql_validator.py
----------------
Validates generated SQL queries for safety before database execution.
Strictly allows only single, read-only SELECT statements (including safe CTEs)
and blocks any modifying, destructive, or multi-statement commands.
"""

import re
from typing import Tuple

# Blacklisted SQL keywords and commands
FORBIDDEN_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "REPLACE",
    "PRAGMA",
    "ATTACH",
    "DETACH",
    "EXEC",
    "EXECUTE",
    "VACUUM",
    "REINDEX",
    "TRANSACTION",
    "COMMIT",
    "ROLLBACK",
]


def strip_sql_comments(sql: str) -> str:
    """Removes single-line (--) and multi-line (/* */) SQL comments."""
    # Remove multi-line comments
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    # Remove single-line comments
    sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    return sql.strip()


def validate_sql(sql_query: str) -> Tuple[bool, str]:
    """
    Validates that a SQL query is safe and strictly read-only.
    
    Rules:
    1. Query must not be empty.
    2. Query must be a single statement (no multiple statements via ';').
    3. Query must start with 'SELECT' or a read-only 'WITH' (CTE).
    4. CTE queries must culminate in a SELECT statement.
    5. Query must not contain forbidden modifying/destructive keywords (INSERT, DROP, UPDATE, etc.).
    
    Returns:
        (is_valid: bool, message: str)
    """
    if not sql_query or not sql_query.strip():
        return False, "Validation Error: SQL query is empty."

    cleaned = strip_sql_comments(sql_query).strip()

    if not cleaned:
        return False, "Validation Error: SQL query contains no executable statements."

    # Remove single trailing semicolon if present
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()

    # Check for multiple statements (semicolon inside query)
    if ";" in cleaned:
        return False, "Validation Error: Multiple SQL statements are not permitted for security reasons."

    # Ensure the statement starts with SELECT or safe read-only CTE (WITH)
    first_token = cleaned.split()[0].upper()
    if first_token not in ("SELECT", "WITH"):
        return (
            False,
            f"Validation Error: Only SELECT queries (or read-only CTE queries) are permitted. Found statement starting with '{first_token}'.",
        )

    # If the query uses a CTE (WITH), verify it culminates in a SELECT statement
    if first_token == "WITH":
        if not re.search(r"\bSELECT\b", cleaned, re.IGNORECASE):
            return False, "Validation Error: CTE queries must culminate in a read-only SELECT statement."

    # Check for forbidden keywords using whole-word boundaries
    for keyword in FORBIDDEN_KEYWORDS:
        pattern = rf"\b{keyword}\b"
        if re.search(pattern, cleaned, flags=re.IGNORECASE):
            return False, f"Validation Error: Disallowed keyword '{keyword}' detected in query."

    return True, "Query passed safety validation (Safe SELECT statement)."
