"""
response_formatter.py
---------------------
Converts raw query execution results into:
1. Structured tabular data formatted for display in UI tables.
2. Clear, natural-language human-readable answers.
Kept strictly decoupled from database execution logic.
"""

from typing import Any, Dict, List


def format_table_data(columns: List[str], rows: List[List[Any]]) -> Dict[str, Any]:
    """
    Prepares tabular data for frontend rendering.
    Handles NULL values and formatting for display.
    """
    formatted_rows = []
    for row in rows:
        formatted_row = []
        for val in row:
            if val is None:
                formatted_row.append("NULL")
            elif isinstance(val, float):
                # Format floating point numbers to 2 decimal places if clean
                formatted_row.append(f"{val:,.2f}" if val != int(val) else str(int(val)))
            elif isinstance(val, int):
                formatted_row.append(f"{val:,}")
            else:
                formatted_row.append(str(val))
        formatted_rows.append(formatted_row)

    return {
        "headers": columns,
        "rows": formatted_rows,
        "row_count": len(rows),
    }


def format_natural_language_answer(
    question: str, columns: List[str], rows: List[List[Any]]
) -> str:
    """
    Synthesizes a concise, human-readable summary of the SQL results
    tailored to the user's natural language question.
    """
    num_rows = len(rows)
    num_cols = len(columns)

    if num_rows == 0:
        return "No records were found in the database matching your question."

    # Case 1: Single scalar value (e.g., COUNT, AVG, MAX, MIN, SUM)
    if num_rows == 1 and num_cols == 1:
        col_name = columns[0]
        val = rows[0][0]
        display_val = f"{val:,.2f}" if isinstance(val, float) else f"{val:,}" if isinstance(val, int) else str(val)
        return f"The result is **{display_val}** ({col_name})."

    # Case 2: Single record with multiple attributes
    if num_rows == 1 and num_cols > 1:
        details = ", ".join(f"**{col}**: {val}" for col, val in zip(columns, rows[0]))
        return f"Found 1 matching record: {details}."

    # Case 3: Multiple rows with a single column (e.g., list of names or titles)
    if num_rows <= 5 and num_cols == 1:
        items = [str(r[0]) for r in rows]
        return f"Found {num_rows} results: " + ", ".join(items) + "."

    # Case 4: Multiple rows
    if num_rows <= 5:
        return f"Found {num_rows} records matching your query. See the table below for full details."
    
    return f"Found **{num_rows:,} records** matching your query. The full dataset is presented in the table below."
