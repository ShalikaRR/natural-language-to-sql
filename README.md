# Natural Language to SQL Query System

An interactive, web-based Natural Language to SQL query translation system built with Python, Flask, and Google Gemini LLM. Query the built-in **Spider 1.0 `college_2`** university database or **upload and explore your own custom SQLite databases**.

---

## 1. Project Overview

This project allows non-technical users and database administrators to query relational databases in plain English (e.g., *"Which instructors in the Computer Science department earn more than 70,000?"* or *"How many orders were placed last month?"*).

The system:
1. **Accepts custom SQLite databases** (`.db`, `.sqlite`, `.sqlite3` up to 25 MB) or uses the default **`college_2`** demo database.
2. **Dynamically inspects the database schema** (tables, columns, types, primary keys, foreign keys, row counts) and provides an interactive Schema Explorer in the UI.
3. **Constructs schema-grounded prompts** and translates the natural-language question into standard SQLite syntax using the configured Gemini model (`gemini-3.8-flash`, `gemini-2.5-flash`, etc.).
4. **Validates the generated SQL** through a multi-tier safety gate (ensuring queries are strictly read-only `SELECT` or `WITH ... SELECT` statements, blocking DDL/DML injection).
5. **Executes the query safely** against the active database using SQLite's read-only mode (`?mode=ro`).
6. **Synthesizes both a human-readable AI summary** and an interactive, sortable results table.

---

## 2. Key Features

- **Custom Database Uploads**: Drag & drop or select any SQLite database (`.sqlite`, `.db`, `.sqlite3`) up to 25 MB.
- **Dynamic Schema Inspection**: Discovers actual tables, columns, data types, and primary keys automatically. Never assumes a fixed table structure.
- **Interactive Schema Explorer**: Collapsible accordion displaying all tables, row counts, and column definitions for the active database.
- **Demo & Custom Database Switching**: Easily switch between your uploaded database and the built-in `college_2` demo database with a single click.
- **Multi-Tier Security & Safety Gate**:
  - Rejects non-SQLite files via binary magic header inspection (`b"SQLite format 3\x00"`).
  - Enforces read-only SQLite engine connections (`file:<path>?mode=ro`).
  - AST / regex validation permits single `SELECT` and `WITH ... SELECT` CTEs while blocking all modifying statements (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, `DETACH`, `PRAGMA`, etc.).
- **Configurable LLM Models & Quota Resilience**:
  - Model choice configurable via `LLM_MODEL` in `.env` (defaults to `gemini-3.8-flash`).
  - Graceful handling of HTTP 429 quota exhaustion with user-friendly alerts.
- **Polished AI Dashboard**: Responsive dark navy/slate dashboard styled with modern Bootstrap 5 and JetBrains Mono typography.

---

## 3. System Architecture

```
User Browser / Web Interface
    │  (Natural Language Question + Active Database ID)
    ▼
Flask Application (app.py)
    │
    ├──► Upload Manager (upload_manager.py)
    │        ├─ Validates extensions (.db, .sqlite, .sqlite3)
    │        ├─ Enforces 25 MB file size limit
    │        ├─ Inspects binary magic header (b"SQLite format 3\x00")
    │        └─ Runs PRAGMA integrity_check
    │
    ├──► Dynamic Schema Extractor (schema.py)
    │        └─ Queries sqlite_master & table_info for tables, columns & row counts
    │
    ├──► LLM Query Generator (nl_to_sql.py)
    │        └─ Google Gemini API via official google-genai SDK
    │
    ├──► SQL Safety Validator (sql_validator.py)
    │        └─ Enforces single SELECT & WITH CTEs, blocks all DDL/DML keywords
    │
    ├──► Read-Only Database Connector (database.py)
    │        └─ Opens SQLite via URI file:<path>?mode=ro
    │
    └──► Response Formatter (response_formatter.py)
             └─ Generates natural-language answer + sanitized tabular records
```

### Module Breakdown

| Module | Responsibility |
|---|---|
| `app.py` | Flask server, route orchestration (`/query`, `/api/upload`, `/api/reset-database`, `/api/database-info`, `/api/schema`). |
| `upload_manager.py` | Manages uploaded SQLite databases, size checks, binary header validation, UUID naming, cleanup routines. |
| `database.py` | Executes queries against any designated SQLite file in strict read-only mode (`?mode=ro`). |
| `schema.py` | Dynamically extracts schema definitions, table lists, and builds prompt context for the LLM. |
| `nl_to_sql.py` | Formulates Gemini prompts with the target database schema, generates SQL, cleans code fences, handles 429 quotas. |
| `sql_validator.py` | Security gate enforcing single-statement read-only `SELECT` and `WITH` queries. |
| `response_formatter.py` | Formats database rows and columns into conversational summaries and UI tables. |
| `templates/index.html` | Academic/AI dashboard UI with drag-and-drop upload, schema explorer, sample queries, and results. |

---

## 4. Databases

### Default Demo: `college_2`
The built-in demo database is **Spider 1.0 `college_2`** (`spider_data/database/college_2/college_2.sqlite`), containing **11 relational tables** and **34,620 rows**:
* `department` (20 rows) — Academic departments and budgets.
* `classroom` (30 rows) — Building locations and room capacities.
* `course` (200 rows) — Courses and credit hours.
* `prereq` (100 rows) — Course prerequisite relationships.
* `instructor` (50 rows) — Faculty members, departments, and salaries.
* `section` (100 rows) — Course offerings by term, year, and classroom.
* `teaches` (100 rows) — Instructor-to-section teaching assignments.
* `student` (2,000 rows) — Student records and total completed credits.
* `advisor` (2,000 rows) — Student academic advisors.
* `takes` (30,000 rows) — Student enrollments and earned grades.
* `time_slot` (20 rows) — Weekly class schedules.

### Custom Uploaded Databases
Users can upload any valid SQLite database. The system automatically inspects the uploaded file, loads the new schema into the LLM context, and updates the UI's sample queries and Schema Explorer.

---

## 5. Security & Safety Guarantees

1. **File Upload Security (`upload_manager.py`)**:
   - Files are stored with cryptographically secure UUID filenames in an `uploads/` staging directory.
   - Files are checked against the canonical 16-byte SQLite header (`b"SQLite format 3\x00"`). Corrupt files, executable files, and scripts are rejected immediately.
   - A strict 25 MB size limit prevents server disk exhaustion.
2. **Pre-Execution SQL Validation (`sql_validator.py`)**:
   - Rejects queries with multiple statements separated by semicolons.
   - Enforces queries starting strictly with `SELECT` or `WITH` (for Common Table Expressions).
   - Prohibits all destructive keywords:
     `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `REPLACE`, `PRAGMA`, `ATTACH`, `DETACH`, `EXEC`, `VACUUM`.
3. **Database Engine Read-Only Enforcement (`database.py`)**:
   - Connections open via `file:<path>?mode=ro` with `uri=True`.
   - SQLite enforces read-only mode at the C-engine / file level.

---

## 6. Setup & Installation

### Step 1: Clone the Repository
```bash
git clone https://github.com/<your-username>/<your-repo-name>.git
cd "NL_SQL Project"
```

### Step 2: Set Up Python Virtual Environment
```bash
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Configure Environment Variables
Copy `.env.example` to create your `.env` file:
```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Open `.env` in any text editor:
```env
# Google Gemini API Key from https://aistudio.google.com/
GEMINI_API_KEY=your_actual_gemini_api_key_here

# LLM Model Name (e.g. gemini-3.8-flash, gemini-2.5-flash)
LLM_MODEL=gemini-3.8-flash

# Flask Server Port & Debug
FLASK_PORT=5000
FLASK_DEBUG=True
```

> [!IMPORTANT]
> Never commit your `.env` file or actual `GEMINI_API_KEY` to GitHub. The `.gitignore` file automatically excludes `.env`.

---

## 7. Running the Website

Start the Flask server:
```bash
python app.py
```

Then open your browser and navigate to:
```
http://127.0.0.1:5000
```

---

## 8. Managing Gemini Free-Tier Quota

The Gemini free-tier includes strict rate limits (e.g., `gemini-3.8-flash` has a 20 request/day limit on free-tier preview).

- If your quota resets or exhausts, the web UI displays a clear notification:
  > *"Gemini API daily quota exceeded. Please try again after the quota resets."*
- To change the active model without code changes, update `LLM_MODEL` in `.env`:
  ```env
  LLM_MODEL=gemini-2.5-flash
  ```

---

## 9. GitHub & Dataset Guidelines

- **Spider Dataset Size Consideration**: The complete Spider 1.0 training dataset is **~1.75 GB (over 900 files)**, which exceeds GitHub's 100 MB file limit.
- **Repository Optimization**: The `.gitignore` file is pre-configured to exclude all unnecessary dataset folders while preserving the lightweight `spider_data/database/college_2/college_2.sqlite` (2.02 MB) so anyone cloning the repository can immediately run the demo without extra downloads.
- **Custom Databases**: All uploaded files are stored in `uploads/` and are ignored by git to keep user data private.

---

## 10. Example Questions

### Built-in `college_2` Demo:
- *"How many students are there?"*
- *"List all departments ordered by budget descending."*
- *"Which instructors in Comp. Sci. earn more than 70,000?"*
- *"What are the top 5 courses offering the highest number of credits?"*
- *"Find the average instructor salary in each department."*

### Custom Uploads:
- *"Show the first 10 records from &lt;your_table&gt;."*
- *"How many entries are recorded in &lt;your_table&gt;?"*
- *"What is the distribution of &lt;category_column&gt; in &lt;your_table&gt;?"*
