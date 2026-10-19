"""
upload_manager.py
-----------------
Manages user-uploaded SQLite databases:
- Enforces file extension (.db, .sqlite, .sqlite3) and 25 MB size limits.
- Validates SQLite header magic bytes (b"SQLite format 3\\x00").
- Performs read-only integrity checks and table discovery.
- Generates secure server-side UUID filenames to prevent path traversal.
- Stores uploads in a dedicated 'uploads/' directory outside public/static paths.
- Provides session/id-based lookup and safe cleanup.
"""

import os
import re
import time
import uuid
import sqlite3
from typing import Any, Dict, Optional, Tuple
from werkzeug.utils import secure_filename
from database import DB_PATH as DEFAULT_DB_PATH

# Upload configuration
UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "uploads"))
ALLOWED_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB
SQLITE_MAGIC_HEADER = b"SQLite format 3\x00"

# In-memory registry of active uploaded databases
# Format: { db_id: { "db_id": ..., "filename": ..., "path": ..., "size": ..., "uploaded_at": ... } }
_ACTIVE_DATABASES: Dict[str, Dict[str, Any]] = {}


def ensure_upload_dir() -> str:
    """Ensures the dedicated uploads directory exists."""
    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR, exist_ok=True)
    return UPLOAD_DIR


def is_allowed_extension(filename: str) -> bool:
    """Checks whether the file has a supported SQLite extension (.db, .sqlite, .sqlite3)."""
    _, ext = os.path.splitext(filename.lower())
    return ext in ALLOWED_EXTENSIONS


def validate_sqlite_file(file_path: str) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Performs comprehensive security and integrity validation on an SQLite file:
    1. Checks file existence and non-zero size.
    2. Validates SQLite format 3 magic header.
    3. Opens strictly in read-only mode to prevent script execution / triggers.
    4. Checks database integrity.
    5. Discovers tables and ensures at least one user table exists.
    """
    if not os.path.exists(file_path):
        return False, "Database file does not exist on server.", {}

    file_size = os.path.getsize(file_path)
    if file_size == 0:
        return False, "The uploaded file is empty (0 bytes).", {}

    if file_size > MAX_FILE_SIZE:
        return False, f"File size ({file_size / (1024*1024):.1f} MB) exceeds the 25 MB limit.", {}

    # Validate the 16-byte SQLite header magic bytes
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)
            if header != SQLITE_MAGIC_HEADER:
                return (
                    False,
                    "Invalid file format. The file is not a valid SQLite 3 database (magic header mismatch).",
                    {},
                )
    except Exception as e:
        return False, f"Failed to read file header: {str(e)}", {}

    # Open strictly in read-only mode to inspect structure
    formatted_path = file_path.replace("\\", "/")
    uri = f"file:{formatted_path}?mode=ro"

    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        cursor = conn.cursor()

        # Run quick integrity check
        cursor.execute("PRAGMA integrity_check(1);")
        check_res = cursor.fetchone()
        if not check_res or str(check_res[0]).lower() != "ok":
            conn.close()
            return False, f"Database integrity check failed: {check_res}", {}

        # Discover tables
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            conn.close()
            return (
                False,
                "The database contains no user tables. Please upload an SQLite database with tables and data.",
                {},
            )

        # Retrieve table metadata (columns, row counts)
        tables_summary = []
        for t in tables:
            cursor.execute(f'PRAGMA table_info("{t}");')
            cols = [col[1] for col in cursor.fetchall()]
            try:
                cursor.execute(f'SELECT count(*) FROM "{t}";')
                count = cursor.fetchone()[0]
            except Exception:
                count = 0
            tables_summary.append({
                "name": t,
                "columns": cols,
                "column_count": len(cols),
                "row_count": count
            })

        conn.close()

        metadata = {
            "table_count": len(tables),
            "tables": tables,
            "tables_summary": tables_summary,
            "size_bytes": file_size,
        }
        return True, "Valid SQLite database.", metadata

    except sqlite3.DatabaseError as db_err:
        return False, f"Corrupted or encrypted SQLite database: {str(db_err)}", {}
    except Exception as exc:
        return False, f"Failed to inspect database: {str(exc)}", {}


def save_uploaded_database(file_storage) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Saves and validates an uploaded file:
    - Generates a cryptographically random UUID server filename.
    - Saves strictly to the dedicated 'uploads/' folder.
    - Validates integrity and discovers schema.
    - If invalid, deletes the file immediately.
    - Registers the file in the active databases dictionary.
    """
    ensure_upload_dir()

    if not file_storage or not file_storage.filename:
        return False, "No file was selected for upload.", {}

    original_name = file_storage.filename.strip()
    if not is_allowed_extension(original_name):
        return (
            False,
            "Unsupported file extension. Only .sqlite, .db, and .sqlite3 files are allowed.",
            {},
        )

    # Sanitize and create random server filename
    safe_base = secure_filename(original_name)
    if not safe_base:
        safe_base = "uploaded_database.sqlite"

    db_id = uuid.uuid4().hex
    server_filename = f"{db_id}_{safe_base}"
    target_path = os.path.abspath(os.path.join(UPLOAD_DIR, server_filename))

    # Path traversal safety check
    if not target_path.startswith(UPLOAD_DIR):
        return False, "Security violation: Invalid upload file path.", {}

    try:
        # Save file to disk
        file_storage.save(target_path)

        # Validate database integrity and schema
        is_valid, msg, metadata = validate_sqlite_file(target_path)
        if not is_valid:
            if os.path.exists(target_path):
                os.remove(target_path)
            return False, msg, {}

        # Register active upload
        upload_record = {
            "db_id": db_id,
            "filename": original_name,
            "server_filename": server_filename,
            "path": target_path,
            "size_bytes": metadata.get("size_bytes", os.path.getsize(target_path)),
            "table_count": metadata.get("table_count", 0),
            "tables": metadata.get("tables", []),
            "tables_summary": metadata.get("tables_summary", []),
            "uploaded_at": time.time(),
        }
        _ACTIVE_DATABASES[db_id] = upload_record

        return True, f"Database '{original_name}' uploaded successfully.", upload_record

    except Exception as e:
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
            except Exception:
                pass
        return False, f"Upload processing failed: {str(e)}", {}


def get_database_path(db_id: Optional[str] = None) -> Tuple[str, str, bool]:
    """
    Resolves the SQLite database file path based on db_id:
    - If db_id is None, empty, or 'default': returns (DEFAULT_DB_PATH, "college_2.sqlite", True)
    - If db_id is an active upload: returns (target_path, original_filename, False)
    - If db_id is not found: falls back to (DEFAULT_DB_PATH, "college_2.sqlite", True)

    Returns:
        (file_path, display_name, is_default)
    """
    if not db_id or db_id == "default":
        return DEFAULT_DB_PATH, "college_2.sqlite", True

    if db_id in _ACTIVE_DATABASES:
        record = _ACTIVE_DATABASES[db_id]
        path = record["path"]
        if os.path.exists(path):
            return path, record["filename"], False

    # Fallback to default
    return DEFAULT_DB_PATH, "college_2.sqlite", True


def get_database_info(db_id: Optional[str] = None) -> Dict[str, Any]:
    """Returns metadata for the requested database (or default if None)."""
    path, display_name, is_default = get_database_path(db_id)

    if is_default:
        is_valid, msg, meta = validate_sqlite_file(path)
        return {
            "db_id": "default",
            "name": "college_2.sqlite",
            "filename": "college_2.sqlite",
            "display_name": "college_2 (Demo Database)",
            "is_default": True,
            "table_count": meta.get("table_count", 11),
            "tables": meta.get("tables", []),
            "tables_summary": meta.get("tables_summary", []),
        }

    record = _ACTIVE_DATABASES.get(db_id, {})
    return {
        "db_id": db_id,
        "name": record.get("filename", display_name),
        "filename": record.get("filename", display_name),
        "display_name": record.get("filename", display_name),
        "is_default": False,
        "table_count": record.get("table_count", 0),
        "tables": record.get("tables", []),
        "tables_summary": record.get("tables_summary", []),
    }


def cleanup_old_uploads(max_age_seconds: int = 86400) -> int:
    """
    Safely removes temporary upload files older than max_age_seconds (default 24h).
    Does not delete active databases currently registered in memory.
    """
    ensure_upload_dir()
    now = time.time()
    deleted_count = 0

    try:
        for fname in os.listdir(UPLOAD_DIR):
            fpath = os.path.join(UPLOAD_DIR, fname)
            if os.path.isfile(fpath):
                # Check file age
                mtime = os.path.getmtime(fpath)
                if (now - mtime) > max_age_seconds:
                    # Remove from registry if present
                    for db_id, rec in list(_ACTIVE_DATABASES.items()):
                        if rec.get("path") == fpath:
                            del _ACTIVE_DATABASES[db_id]
                    try:
                        os.remove(fpath)
                        deleted_count += 1
                    except Exception:
                        pass
    except Exception:
        pass

    return deleted_count
