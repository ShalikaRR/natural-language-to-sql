"""
nl_to_sql.py
------------
Translates natural-language questions into executable SQLite queries using an LLM.
Retrieves database schema dynamically, formats the system prompt, and cleans LLM output.
Supports both default college_2 and user-uploaded databases.
Does NOT execute any SQL queries.
"""

import os
import re
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from schema import get_schema_prompt_text


class QuotaExceededError(RuntimeError):
    """Raised when the Gemini API quota is exhausted (HTTP 429)."""
    pass


def is_quota_error(exc: Exception) -> bool:
    """Detects HTTP 429 / RESOURCE_EXHAUSTED quota errors from the Gemini API."""
    msg = str(exc)
    return "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()


def get_api_key() -> Optional[str]:
    """Retrieves the Gemini API key from environment variables."""
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def get_model_name() -> str:
    """
    Retrieves the model name configured in environment variables.
    Defaults to 'gemini-flash-latest' if not explicitly configured in .env.
    """
    return os.getenv("LLM_MODEL", "gemini-flash-latest").strip()


def clean_generated_sql(raw_output: str) -> str:
    """
    Strips markdown code blocks, backticks, and extraneous text
    from the LLM's response to extract pure SQL.
    """
    text = raw_output.strip()

    # Remove markdown code fences like ```sql ... ``` or ``` ... ```
    match = re.search(r"```(?:sql)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
    if match:
        text = match.group(1).strip()

    # Remove any stray leading/trailing backticks
    text = text.strip("`").strip()

    # If the LLM included introductory text like "SQL: SELECT...", remove the prefix
    if re.match(r"^(?:SQL|Query):\s*", text, flags=re.IGNORECASE):
        text = re.sub(r"^(?:SQL|Query):\s*", "", text, flags=re.IGNORECASE).strip()

    return text


def build_prompt(question: str, schema_text: str) -> str:
    """
    Constructs the prompt containing the SQLite schema, instructions,
    and the user's natural language question.
    """
    prompt = f"""You are an expert SQLite Database Engineer.
Your task is to convert a natural language question into a valid, efficient SQLite query.

{schema_text}

Guidelines:
1. Generate a single SQLite SELECT statement only (or a read-only CTE starting with WITH culminating in SELECT).
2. Do not use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, ATTACH, DETACH, or PRAGMA statements.
3. Use correct table and column names exactly as defined in the schema above.
4. Join tables using the foreign keys or relational columns indicated in the schema.
5. Return ONLY the raw SQL query. Do not wrap in markdown quotes, backticks, or provide explanations.

Natural Language Question: {question}

SQL Query:"""
    return prompt


def generate_sql(
    question: str,
    schema_text: Optional[str] = None,
    db_path: Optional[str] = None,
    db_name: Optional[str] = None,
) -> str:
    """
    Converts a natural language question into a SQL query using the Gemini API.
    
    Args:
        question: The user's query in plain English.
        schema_text: Optional pre-rendered schema text. If omitted, extracted from db_path.
        db_path: Optional path to the targeted SQLite database file.
        db_name: Optional display name for the targeted database.
        
    Returns:
        A cleaned SQLite query string.
        
    Raises:
        ValueError: If the API key is not configured or question is empty.
        QuotaExceededError: If Gemini API quota (HTTP 429) is exhausted.
        RuntimeError: If the LLM API call fails.
    """
    if not question or not question.strip():
        raise ValueError("Question cannot be empty.")

    api_key = get_api_key()
    if not api_key or api_key == "your_gemini_api_key_here":
        raise ValueError(
            "GEMINI_API_KEY is not configured. Please set your Gemini API key in the .env file."
        )

    model_name = get_model_name()
    if not schema_text:
        schema_text = get_schema_prompt_text(db_path=db_path, db_name=db_name)
    prompt = build_prompt(question.strip(), schema_text)

    # Call LLM using google-genai SDK with retry for transient 503 load spikes
    import time
    last_exception = None

    for attempt in range(3):
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            raw_text = response.text or ""
            return clean_generated_sql(raw_text)
        except Exception as e:
            last_exception = e
            # Immediate exit on quota exhaustion (HTTP 429) - do not retry
            if is_quota_error(e):
                raise QuotaExceededError(
                    "Gemini API daily quota exceeded. Please try again after the quota resets."
                ) from e
            # If 503 (temporary high demand), wait briefly and retry
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                time.sleep(1.5 * (attempt + 1))
                continue
            break

    # If the SDK failure was quota-related, raise QuotaExceededError immediately
    if last_exception and is_quota_error(last_exception):
        raise QuotaExceededError(
            "Gemini API daily quota exceeded. Please try again after the quota resets."
        )

    # Fallback to direct HTTP request if SDK fails (non-quota errors)
    try:
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        res = requests.post(url, headers={"x-goog-api-key": api_key}, json=payload, timeout=20)
        if res.status_code == 200:
            data = res.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            return clean_generated_sql(raw_text)
        elif res.status_code == 429 or is_quota_error(res.text):
            raise QuotaExceededError(
                "Gemini API daily quota exceeded. Please try again after the quota resets."
            )
        else:
            err_text = res.text.replace(api_key, "[REDACTED]") if api_key else res.text
            raise RuntimeError(f"Gemini API returned error {res.status_code}: {err_text}")
    except QuotaExceededError:
        raise
    except Exception as inner_e:
        if is_quota_error(inner_e):
            raise QuotaExceededError(
                "Gemini API daily quota exceeded. Please try again after the quota resets."
            ) from inner_e
        err_msg = str(last_exception).replace(api_key, "[REDACTED]") if api_key else str(last_exception)
        inner_msg = str(inner_e).replace(api_key, "[REDACTED]") if api_key else str(inner_e)
        raise RuntimeError(f"Failed to generate SQL from LLM: {err_msg} | Details: {inner_msg}")
