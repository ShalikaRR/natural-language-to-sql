"""
app.py
------
Flask Web Application for the Natural Language to SQL Query System.
Orchestrates:
1. Serving the web interface (templates/index.html).
2. Receiving user-uploaded SQLite databases at POST /api/upload.
3. Resetting / switching active database at POST /api/reset-database.
4. Providing live schema inspection at GET /api/schema & GET /api/database-info.
5. Processing natural-language questions at POST /query.
6. Translating NL to SQL via LLM (nl_to_sql.py).
7. Enforcing strict read-only safety rules via SQL validator (sql_validator.py).
8. Executing validated SELECT queries on the active SQLite database in read-only mode (database.py).
9. Formatting answers and tabular results (response_formatter.py).
"""

import os
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

# Load environment configuration
load_dotenv()

from database import execute_query, test_connection, DB_PATH as DEFAULT_DB_PATH
from schema import extract_schema
from sql_validator import validate_sql
from nl_to_sql import generate_sql, QuotaExceededError
from response_formatter import format_natural_language_answer, format_table_data
from upload_manager import (
    save_uploaded_database,
    get_database_path,
    get_database_info,
    MAX_FILE_SIZE,
    cleanup_old_uploads,
)

app = Flask(__name__)
# Set 25 MB max upload limit at Flask server level
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

# Run cleanup for stale upload files on startup
cleanup_old_uploads()


@app.errorhandler(413)
def request_entity_too_large(error):
    """Handles oversized uploads gracefully."""
    return jsonify({
        "success": False,
        "error": "File size exceeds the 25 MB upload limit. Please upload a smaller SQLite database."
    }), 413


@app.route("/", methods=["GET"])
def index():
    """Serves the main web user interface."""
    return render_template("index.html")


@app.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint verifying database connectivity."""
    db_id = request.args.get("db_id", "").strip() or None
    try:
        target_path, display_name, is_default = get_database_path(db_id)
        db_status = test_connection(target_path)
        db_status["display_name"] = display_name
        db_status["is_default"] = is_default
        return jsonify({
            "status": "healthy",
            "database": db_status
        }), 200
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500


@app.route("/api/schema", methods=["GET"])
def get_schema():
    """API endpoint providing dynamic schema metadata for the active or requested database."""
    db_id = request.args.get("db_id", "").strip() or None
    try:
        target_path, display_name, is_default = get_database_path(db_id)
        schema_data = extract_schema(db_path=target_path)
        schema_data["display_name"] = display_name
        schema_data["is_default"] = is_default
        return jsonify(schema_data), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/database-info", methods=["GET"])
def database_info():
    """Returns metadata and discovered tables for the requested or default database."""
    db_id = request.args.get("db_id", "").strip() or None
    try:
        info = get_database_info(db_id)
        target_path, _, _ = get_database_path(db_id)
        schema_data = extract_schema(db_path=target_path)
        info["tables_metadata"] = schema_data.get("tables", {})
        return jsonify({
            "success": True,
            "database": info
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/upload", methods=["POST"])
def upload_database():
    """
    Handles user-uploaded SQLite databases:
    - Validates file extensions (.db, .sqlite, .sqlite3)
    - Validates file size (<= 25 MB)
    - Validates SQLite 3 binary magic header
    - Runs read-only integrity check and discovers tables
    - Stores file with random UUID server filename
    - Returns database id and schema summary
    """
    if "file" not in request.files:
        return jsonify({
            "success": False,
            "error": "No file field found in upload request. Please choose an SQLite file."
        }), 400

    uploaded_file = request.files["file"]
    if not uploaded_file or not uploaded_file.filename or not uploaded_file.filename.strip():
        return jsonify({
            "success": False,
            "error": "No file was selected. Please select a valid .sqlite, .db, or .sqlite3 file."
        }), 400

    success, message, upload_record = save_uploaded_database(uploaded_file)
    if not success:
        return jsonify({
            "success": False,
            "error": message
        }), 400

    # Extract dynamic schema for the new upload
    try:
        schema_data = extract_schema(db_path=upload_record["path"], force_refresh=True)
        return jsonify({
            "success": True,
            "message": message,
            "db_id": upload_record["db_id"],
            "filename": upload_record["filename"],
            "table_count": upload_record["table_count"],
            "tables": upload_record["tables"],
            "tables_summary": upload_record["tables_summary"],
            "schema": schema_data
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Database uploaded, but schema inspection failed: {str(e)}"
        }), 400


@app.route("/api/reset-database", methods=["POST"])
def reset_database():
    """Resets the active database back to the default college_2 demo database."""
    try:
        default_info = get_database_info("default")
        schema_data = extract_schema(db_path=DEFAULT_DB_PATH)
        return jsonify({
            "success": True,
            "message": "Switched back to default demo database (college_2.sqlite).",
            "db_id": "default",
            "filename": "college_2.sqlite",
            "database": default_info,
            "schema": schema_data
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/query", methods=["POST"])
def handle_query():
    """
    Main endpoint for processing natural-language queries:
    1. Extracts user question and optional target database id.
    2. Resolves target database (uploaded database or default college_2).
    3. Generates SQLite query via LLM using the target database schema.
    4. Validates SQL safety (SELECT / read-only CTE only).
    5. Executes query against read-only target SQLite database.
    6. Formats natural-language answer and tabular output.
    """
    payload = request.get_json(silent=True) or {}
    question = payload.get("question", "").strip()
    db_id = payload.get("db_id", "").strip() or None

    if not question:
        return jsonify({
            "success": False,
            "error": "Please enter a valid natural-language question."
        }), 400

    # Resolve target database path and display name
    target_path, display_name, is_default = get_database_path(db_id)

    generated_sql = None
    try:
        # Step 1: Generate SQL from Natural Language with target database schema
        generated_sql = generate_sql(
            question,
            db_path=target_path,
            db_name=display_name
        )

        # Step 2: Validate SQL Safety
        is_valid, validation_msg = validate_sql(generated_sql)
        if not is_valid:
            return jsonify({
                "success": False,
                "question": question,
                "sql": generated_sql,
                "is_valid": False,
                "validation_message": validation_msg,
                "database_name": display_name,
                "error": f"Security Check Failed: {validation_msg}"
            }), 400

        # Step 3: Execute validated query on read-only SQLite database
        raw_result = execute_query(generated_sql, db_path=target_path)

        # Step 4: Format outputs
        table_result = format_table_data(raw_result["columns"], raw_result["rows"])
        nl_answer = format_natural_language_answer(
            question, raw_result["columns"], raw_result["rows"]
        )

        return jsonify({
            "success": True,
            "question": question,
            "sql": generated_sql,
            "is_valid": True,
            "validation_message": validation_msg,
            "columns": table_result["headers"],
            "rows": table_result["rows"],
            "row_count": table_result["row_count"],
            "summary": nl_answer,
            "database_name": display_name,
            "is_default_database": is_default
        }), 200

    except QuotaExceededError as q_err:
        return jsonify({
            "success": False,
            "question": question,
            "sql": None,
            "database_name": display_name,
            "error": "Gemini API daily quota exceeded. Please try again after the quota resets."
        }), 429

    except ValueError as val_err:
        return jsonify({
            "success": False,
            "question": question,
            "sql": generated_sql,
            "database_name": display_name,
            "error": str(val_err)
        }), 400

    except Exception as exc:
        err_msg = str(exc)
        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "quota" in err_msg.lower():
            return jsonify({
                "success": False,
                "question": question,
                "sql": None,
                "database_name": display_name,
                "error": "Gemini API daily quota exceeded. Please try again after the quota resets."
            }), 429
        return jsonify({
            "success": False,
            "question": question,
            "sql": generated_sql,
            "database_name": display_name,
            "error": f"Execution error: {err_msg}"
        }), 500


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "True").lower() in ("true", "1", "yes")
    print(f"Starting Natural Language to SQL Web Application on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=debug)
