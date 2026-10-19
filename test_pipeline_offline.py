import io
import os
import sqlite3
import unittest
from unittest.mock import patch
from werkzeug.datastructures import FileStorage

# Import project modules
from app import app
from upload_manager import save_uploaded_database, get_database_path, get_database_info, cleanup_old_uploads
from schema import extract_schema, get_schema_prompt_text
from sql_validator import validate_sql
from database import execute_query, test_connection, DB_PATH as DEFAULT_DB_PATH


class TestOfflinePipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Create a temporary custom SQLite database for testing
        cls.scratch_dir = os.path.join(os.path.dirname(__file__), "scratch")
        os.makedirs(cls.scratch_dir, exist_ok=True)
        cls.test_db_path = os.path.join(cls.scratch_dir, "test_inventory.sqlite")

        if os.path.exists(cls.test_db_path):
            os.remove(cls.test_db_path)

        conn = sqlite3.connect(cls.test_db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL);")
        cur.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price REAL, category_id INTEGER);")
        cur.execute("INSERT INTO categories VALUES (1, 'Electronics'), (2, 'Books');")
        cur.execute("INSERT INTO products VALUES (101, 'Laptop', 1200.0, 1), (102, 'Textbook', 75.0, 2);")
        conn.commit()
        conn.close()

        # Check default database exists
        assert os.path.exists(DEFAULT_DB_PATH), f"college_2.sqlite not found at {DEFAULT_DB_PATH}"

    def test_01_sql_validator(self):
        """Verify that sql_validator accepts valid SELECT and CTE queries, but rejects unsafe operations."""
        # Valid queries
        v1, _ = validate_sql("SELECT * FROM products;")
        self.assertTrue(v1)

        v2, _ = validate_sql("SELECT count(*) as total, category_id FROM products GROUP BY category_id;")
        self.assertTrue(v2)

        v3, _ = validate_sql("WITH expensive AS (SELECT * FROM products WHERE price > 100) SELECT * FROM expensive;")
        self.assertTrue(v3)

        # Invalid queries
        inv1, m1 = validate_sql("DROP TABLE products;")
        self.assertFalse(inv1)

        inv2, m2 = validate_sql("INSERT INTO products VALUES (103, 'Phone', 800, 1);")
        self.assertFalse(inv2)

        inv3, m3 = validate_sql("SELECT * FROM products; DROP TABLE products;")
        self.assertFalse(inv3)

        inv4, m4 = validate_sql("PRAGMA table_info(products);")
        self.assertFalse(inv4)

        inv5, m5 = validate_sql("ATTACH DATABASE 'test.db' AS test;")
        self.assertFalse(inv5)

    def test_02_upload_manager_validation(self):
        """Test file validation: invalid extensions, corrupt headers, valid SQLite."""
        # 1. Invalid extension
        txt_file = FileStorage(
            stream=io.BytesIO(b"Hello world"),
            filename="test.txt",
            content_type="text/plain"
        )
        ok, msg, _ = save_uploaded_database(txt_file)
        self.assertFalse(ok)
        self.assertIn("Unsupported file extension", msg)

        # 2. Corrupt header (named .sqlite but not SQLite format 3)
        fake_sqlite = FileStorage(
            stream=io.BytesIO(b"Not an SQLite database file at all!"),
            filename="fake.sqlite",
            content_type="application/octet-stream"
        )
        ok, msg, _ = save_uploaded_database(fake_sqlite)
        self.assertFalse(ok)
        self.assertIn("not a valid SQLite 3 database", msg)

        # 3. Valid database upload
        with open(self.test_db_path, "rb") as f:
            valid_file = FileStorage(
                stream=io.BytesIO(f.read()),
                filename="inventory_upload.sqlite",
                content_type="application/x-sqlite3"
            )
        ok, msg, record = save_uploaded_database(valid_file)
        self.assertTrue(ok)
        self.assertEqual(record["table_count"], 2)
        self.assertIn("categories", record["tables"])
        self.assertIn("products", record["tables"])

        # Store for subsequent test
        self.__class__.uploaded_db_id = record["db_id"]

    def test_03_schema_extraction_custom_db(self):
        """Test that schema extraction accurately reads custom database tables and columns."""
        schema = extract_schema(self.test_db_path)
        self.assertIn("products", schema["tables"])
        self.assertIn("categories", schema["tables"])

        prod_cols = [c["name"] for c in schema["tables"]["products"]["columns"]]
        self.assertIn("id", prod_cols)
        self.assertIn("name", prod_cols)
        self.assertIn("price", prod_cols)
        self.assertIn("category_id", prod_cols)

        # Verify prompt text generation includes the custom tables
        prompt_text = get_schema_prompt_text(self.test_db_path, "test_inventory")
        self.assertIn("Table: products", prompt_text)
        self.assertIn("Table: categories", prompt_text)

    def test_04_read_only_database_execution(self):
        """Verify execution on custom database is read-only and returns correct data."""
        res = execute_query("SELECT count(*) as cnt FROM products;", db_path=self.test_db_path)
        self.assertEqual(res["columns"], ["cnt"])
        self.assertEqual(res["rows"], [[2]])

    def test_05_flask_api_routes(self):
        """Test Flask routes with test client: /api/database-info, /api/upload, /query (mocked LLM)."""
        client = app.test_client()

        # 1. Main UI page loads
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Natural Language SQL Assistant", resp.data)
        self.assertIn(b"Schema Explorer", resp.data)
        self.assertIn(b"Database Workspace", resp.data)

        # 2. Database info for default demo
        resp = client.get("/api/database-info?db_id=default")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["database"]["filename"], "college_2.sqlite")
        self.assertEqual(data["database"]["table_count"], 11)

        # 3. Upload through Flask endpoint
        with open(self.test_db_path, "rb") as f:
            upload_data = {
                "file": (io.BytesIO(f.read()), "client_test.sqlite")
            }
            resp = client.post("/api/upload", data=upload_data, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 200)
        upload_resp = resp.get_json()
        self.assertTrue(upload_resp["success"])
        new_db_id = upload_resp["db_id"]

        # 4. Query custom database with mocked Gemini generation (NO API CALLS)
        mock_sql = "SELECT name, price FROM products ORDER BY price DESC;"
        with patch("app.generate_sql", return_value=mock_sql):
            resp = client.post("/query", json={
                "question": "Show all products ordered by price",
                "db_id": new_db_id
            })
            self.assertEqual(resp.status_code, 200)
            q_res = resp.get_json()
            self.assertTrue(q_res["success"])
            self.assertEqual(q_res["sql"], mock_sql)
            self.assertEqual(q_res["row_count"], 2)
            self.assertEqual(q_res["columns"], ["name", "price"])
            self.assertEqual(q_res["rows"][0], ["Laptop", "1200"])

        # 5. Reset to default demo database
        resp = client.post("/api/reset-database")
        self.assertEqual(resp.status_code, 200)
        reset_res = resp.get_json()
        self.assertTrue(reset_res["success"])
        self.assertEqual(reset_res["db_id"], "default")

    def test_06_verify_college_2_integrity(self):
        """Verify that college_2.sqlite has remained untouched and readable."""
        status = test_connection(DEFAULT_DB_PATH)
        self.assertTrue(status["connected"])
        self.assertEqual(status["table_count"], 11)
        res = execute_query("SELECT count(*) FROM student;", db_path=DEFAULT_DB_PATH)
        self.assertEqual(res["rows"], [[2000]])


if __name__ == "__main__":
    unittest.main()
