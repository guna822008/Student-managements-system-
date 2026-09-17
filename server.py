#!/usr/bin/env python3
"""
Student Register — backend API server.

A dependency-free REST API (Python standard library only) backed by SQLite.
Serves the static frontend and exposes a JSON CRUD API for student records.

Run:
    python3 server.py [port]        # default port 8000

Endpoints:
    GET    /api/students            list students (optional ?q=search)
    GET    /api/students/<id>       get one student
    POST   /api/students            create a student
    PUT    /api/students/<id>       update a student
    DELETE /api/students/<id>       delete a student
"""

import json
import os
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "frontend"))
DB_PATH = os.path.join(BASE_DIR, "students.db")

REQUIRED_FIELDS = ["name", "roll_no", "email", "course", "year", "marks"]

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT NOT NULL,
            roll_no  TEXT NOT NULL UNIQUE,
            email    TEXT NOT NULL,
            course   TEXT NOT NULL,
            year     INTEGER NOT NULL,
            marks    REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()

    # Seed with a few example rows on first run so the register isn't empty.
    count = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
    if count == 0:
        sample = [
            ("Asha Nair", "R-2024-001", "asha.nair@example.edu", "B.Sc. Computer Science", 2, 88.5),
            ("Devika Menon", "R-2024-002", "devika.menon@example.edu", "B.Com. Finance", 3, 76.0),
            ("Karthik Iyer", "R-2024-003", "karthik.iyer@example.edu", "B.Tech Electronics", 1, 91.2),
        ]
        conn.executemany(
            "INSERT INTO students (name, roll_no, email, course, year, marks) VALUES (?, ?, ?, ?, ?, ?)",
            sample,
        )
        conn.commit()
    conn.close()


def row_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "roll_no": row["roll_no"],
        "email": row["email"],
        "course": row["course"],
        "year": row["year"],
        "marks": row["marks"],
    }


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_payload(data, partial=False):
    errors = []
    fields = REQUIRED_FIELDS if not partial else [f for f in REQUIRED_FIELDS if f in data]

    for field in fields:
        if field not in data or data[field] in (None, ""):
            errors.append(f"'{field}' is required.")

    if "email" in data and data["email"] and not EMAIL_RE.match(str(data["email"])):
        errors.append("'email' must be a valid email address.")

    if "year" in data and data["year"] not in (None, ""):
        try:
            year_val = int(data["year"])
            if year_val < 1 or year_val > 8:
                errors.append("'year' must be between 1 and 8.")
        except (ValueError, TypeError):
            errors.append("'year' must be a whole number.")

    if "marks" in data and data["marks"] not in (None, ""):
        try:
            marks_val = float(data["marks"])
            if marks_val < 0 or marks_val > 100:
                errors.append("'marks' must be between 0 and 100.")
        except (ValueError, TypeError):
            errors.append("'marks' must be a number.")

    return errors


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "StudentRegister/1.0"

    # ---- utility responses ----
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, message, errors=None):
        payload = {"error": message}
        if errors:
            payload["details"] = errors
        self._send_json(status, payload)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        safe_path = os.path.normpath(path).lstrip("/")
        file_path = os.path.join(FRONTEND_DIR, safe_path)

        if not file_path.startswith(FRONTEND_DIR) or not os.path.isfile(file_path):
            self._send_error_json(404, "Not found.")
            return

        ext = os.path.splitext(file_path)[1]
        content_type = MIME_TYPES.get(ext, "application/octet-stream")
        with open(file_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- HTTP verbs ----
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        segments = [s for s in parsed.path.split("/") if s]

        if segments[:2] == ["api", "students"]:
            conn = get_conn()
            try:
                if len(segments) == 2:
                    query = parse_qs(parsed.query)
                    search = query.get("q", [""])[0].strip()
                    if search:
                        like = f"%{search}%"
                        rows = conn.execute(
                            """SELECT * FROM students
                               WHERE name LIKE ? OR roll_no LIKE ? OR course LIKE ? OR email LIKE ?
                               ORDER BY id DESC""",
                            (like, like, like, like),
                        ).fetchall()
                    else:
                        rows = conn.execute("SELECT * FROM students ORDER BY id DESC").fetchall()
                    self._send_json(200, [row_to_dict(r) for r in rows])
                elif len(segments) == 3:
                    student_id = segments[2]
                    row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
                    if row is None:
                        self._send_error_json(404, "Student not found.")
                    else:
                        self._send_json(200, row_to_dict(row))
                else:
                    self._send_error_json(404, "Not found.")
            finally:
                conn.close()
            return

        self._serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        segments = [s for s in parsed.path.split("/") if s]

        if segments == ["api", "students"]:
            data = self._read_json_body()
            if data is None:
                self._send_error_json(400, "Malformed JSON body.")
                return
            errors = validate_payload(data)
            if errors:
                self._send_error_json(422, "Validation failed.", errors)
                return
            conn = get_conn()
            try:
                cur = conn.execute(
                    """INSERT INTO students (name, roll_no, email, course, year, marks)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        data["name"].strip(),
                        data["roll_no"].strip(),
                        data["email"].strip(),
                        data["course"].strip(),
                        int(data["year"]),
                        float(data["marks"]),
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()
                self._send_json(201, row_to_dict(row))
            except sqlite3.IntegrityError:
                self._send_error_json(409, f"Roll number '{data['roll_no']}' already exists.")
            finally:
                conn.close()
            return

        self._send_error_json(404, "Not found.")

    def do_PUT(self):
        parsed = urlparse(self.path)
        segments = [s for s in parsed.path.split("/") if s]

        if segments[:2] == ["api", "students"] and len(segments) == 3:
            student_id = segments[2]
            data = self._read_json_body()
            if data is None:
                self._send_error_json(400, "Malformed JSON body.")
                return
            errors = validate_payload(data, partial=True)
            if errors:
                self._send_error_json(422, "Validation failed.", errors)
                return

            conn = get_conn()
            try:
                existing = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
                if existing is None:
                    self._send_error_json(404, "Student not found.")
                    return

                merged = row_to_dict(existing)
                for field in REQUIRED_FIELDS:
                    if field in data and data[field] not in (None, ""):
                        merged[field] = data[field]

                conn.execute(
                    """UPDATE students SET name=?, roll_no=?, email=?, course=?, year=?, marks=?
                       WHERE id=?""",
                    (
                        str(merged["name"]).strip(),
                        str(merged["roll_no"]).strip(),
                        str(merged["email"]).strip(),
                        str(merged["course"]).strip(),
                        int(merged["year"]),
                        float(merged["marks"]),
                        student_id,
                    ),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
                self._send_json(200, row_to_dict(row))
            except sqlite3.IntegrityError:
                self._send_error_json(409, f"Roll number '{data.get('roll_no')}' already exists.")
            finally:
                conn.close()
            return

        self._send_error_json(404, "Not found.")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        segments = [s for s in parsed.path.split("/") if s]

        if segments[:2] == ["api", "students"] and len(segments) == 3:
            student_id = segments[2]
            conn = get_conn()
            try:
                existing = conn.execute("SELECT id FROM students WHERE id = ?", (student_id,)).fetchone()
                if existing is None:
                    self._send_error_json(404, "Student not found.")
                    return
                conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
                conn.commit()
                self._send_json(200, {"deleted": int(student_id)})
            finally:
                conn.close()
            return

        self._send_error_json(404, "Not found.")

    # Quiet the default request logging down to one clean line.
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Student Register running at http://localhost:{port}")
    print(f"Database file: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
