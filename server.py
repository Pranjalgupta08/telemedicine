#!/usr/bin/env python3
"""Local-only SQLite backend for the Kindred Care demonstration."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from http.cookies import CookieError, SimpleCookie
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("KINDRED_DB_PATH", ROOT / "data" / "kindred_demo.sqlite3"))
MAX_BODY_BYTES = 1_000_000
VALID_STATUSES = {"scheduled", "awaiting-prescription", "completed"}


def connect_database() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    with connect_database() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS demo_patient (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                full_name TEXT NOT NULL,
                date_of_birth TEXT NOT NULL,
                mobile_number TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                date_of_birth TEXT NOT NULL,
                mobile_number TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS profiles_identity_idx
            ON profiles (lower(full_name), date_of_birth, mobile_number)
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO profiles (full_name, date_of_birth, mobile_number)
            SELECT full_name, date_of_birth, mobile_number
            FROM demo_patient WHERE id = 1
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN ('scheduled', 'awaiting-prescription', 'completed')),
                doctor TEXT NOT NULL,
                data_json TEXT NOT NULL,
                profile_id INTEGER REFERENCES profiles(id),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        appointment_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(appointments)")
        }
        if "profile_id" not in appointment_columns:
            connection.execute(
                "ALTER TABLE appointments ADD COLUMN profile_id INTEGER REFERENCES profiles(id)"
            )
        connection.execute(
            """
            UPDATE appointments
            SET profile_id = (SELECT id FROM profiles ORDER BY id LIMIT 1)
            WHERE profile_id IS NULL
              AND EXISTS (SELECT 1 FROM profiles)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                profile_id INTEGER NOT NULL REFERENCES profiles(id),
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (profile_id, key)
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO user_preferences (profile_id, key, value_json)
            SELECT profiles.id, preferences.key, preferences.value_json
            FROM profiles CROSS JOIN preferences
            WHERE preferences.key = 'reminders'
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO user_preferences (profile_id, key, value_json)
            SELECT id, 'reminders', 'false' FROM profiles
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS medication_checks (
                check_date TEXT NOT NULL,
                medicine_id TEXT NOT NULL,
                taken INTEGER NOT NULL CHECK(taken IN (0, 1)),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (check_date, medicine_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_medication_checks (
                profile_id INTEGER NOT NULL REFERENCES profiles(id),
                check_date TEXT NOT NULL,
                medicine_id TEXT NOT NULL,
                taken INTEGER NOT NULL CHECK(taken IN (0, 1)),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (profile_id, check_date, medicine_id)
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO user_medication_checks (profile_id, check_date, medicine_id, taken, updated_at)
            SELECT profiles.id, medication_checks.check_date, medication_checks.medicine_id,
                   medication_checks.taken, medication_checks.updated_at
            FROM profiles CROSS JOIN medication_checks
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "KindredDemo/1.0"

    def _send_json(self, status: int, value: object, headers: dict[str, str] | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, header_value in (headers or {}).items():
            self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(payload)

    def _session_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookie.get("kindred_session")
        return morsel.value if morsel else None

    def _session_cookie(self, token: str, clear: bool = False) -> str:
        parts = [f"kindred_session={token}" if not clear else "kindred_session=", "Path=/", "HttpOnly", "SameSite=Strict"]
        if self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip() == "https":
            parts.append("Secure")
        if clear:
            parts.extend(("Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"))
        else:
            parts.append("Max-Age=2592000")
        return "; ".join(parts)

    def _authenticated_profile(self) -> sqlite3.Row:
        token = self._session_token()
        if token is None:
            raise ApiError(401, "Sign in to access this demo profile.")
        with connect_database() as connection:
            profile = connection.execute(
                """
                SELECT profiles.id, profiles.full_name
                FROM sessions JOIN profiles ON profiles.id = sessions.profile_id
                WHERE sessions.token = ?
                """,
                (token,),
            ).fetchone()
        if profile is None:
            raise ApiError(401, "Your demo session has expired. Please sign in again.")
        return profile

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ApiError(400, "Content-Length must be a number.") from error
        if length < 1 or length > MAX_BODY_BYTES:
            raise ApiError(413 if length > MAX_BODY_BYTES else 400, "Request body is empty or too large.")
        if self.headers.get_content_type() != "application/json":
            raise ApiError(415, "Content-Type must be application/json.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ApiError(400, "Request body must contain valid JSON.") from error
        if not isinstance(payload, dict):
            raise ApiError(400, "Request body must be a JSON object.")
        return payload

    def _appointment(self, value: object) -> dict:
        if not isinstance(value, dict):
            raise ApiError(400, "Appointment must be a JSON object.")
        appointment = value
        for key in ("id", "doctor", "date", "time", "format"):
            if not isinstance(appointment.get(key), str) or not appointment[key].strip():
                raise ApiError(400, f"Appointment field '{key}' is required.")
        if appointment.get("status") not in VALID_STATUSES:
            raise ApiError(400, "Appointment status is not valid.")
        if not isinstance(appointment.get("prescriptions", []), list):
            raise ApiError(400, "Appointment prescriptions must be a list.")
        if appointment["status"] != "completed" and appointment.get("prescriptions"):
            raise ApiError(400, "Prescriptions can only be stored for a completed visit.")
        return appointment

    def _handle_demo_profile(self, method: str) -> None:
        if method == "GET":
            token = self._session_token()
            with connect_database() as connection:
                profile = connection.execute(
                    """
                    SELECT profiles.id, profiles.full_name
                    FROM sessions JOIN profiles ON profiles.id = sessions.profile_id
                    WHERE sessions.token = ?
                    """,
                    (token or "",),
                ).fetchone()
                row = connection.execute("SELECT 1 FROM profiles LIMIT 1").fetchone()
            self._send_json(200, {
                "profileExists": row is not None,
                "authenticated": profile is not None,
                "fullName": profile["full_name"] if profile else None,
            })
            return
        if method != "POST":
            raise ApiError(405, "Method is not allowed.")
        profile = self._read_json()
        full_name = profile.get("fullName")
        date_of_birth = profile.get("dateOfBirth")
        mobile_number = profile.get("mobileNumber")
        if not isinstance(full_name, str) or not 2 <= len(full_name.strip()) <= 100:
            raise ApiError(400, "Enter a name between 2 and 100 characters.")
        if not isinstance(date_of_birth, str):
            raise ApiError(400, "Enter a valid date of birth.")
        try:
            birth_date = date.fromisoformat(date_of_birth)
        except ValueError as error:
            raise ApiError(400, "Enter a valid date of birth.") from error
        if birth_date >= date.today():
            raise ApiError(400, "Date of birth must be in the past.")
        if not isinstance(mobile_number, str) or not re.fullmatch(r"\+?[0-9 ()-]{7,20}", mobile_number):
            raise ApiError(400, "Enter a valid mobile number.")
        if sum(character.isdigit() for character in mobile_number) < 7:
            raise ApiError(400, "Mobile number must contain at least 7 digits.")

        clean_name = full_name.strip()
        clean_mobile = re.sub(r"[ ()-]", "", mobile_number)
        token = secrets.token_urlsafe(32)
        with connect_database() as connection:
            matches = connection.execute(
                """
                SELECT id, full_name FROM profiles
                WHERE date_of_birth = ? AND mobile_number = ?
                """,
                (birth_date.isoformat(), clean_mobile),
            ).fetchall()
            row = next(
                (candidate for candidate in matches if candidate["full_name"].casefold() == clean_name.casefold()),
                None,
            )
            created = row is None
            if created:
                cursor = connection.execute(
                    "INSERT INTO profiles (full_name, date_of_birth, mobile_number) VALUES (?, ?, ?)",
                    (clean_name, birth_date.isoformat(), clean_mobile),
                )
                profile_id = cursor.lastrowid
            else:
                profile_id = row["id"]
                clean_name = row["full_name"]
            connection.execute(
                """
                INSERT OR IGNORE INTO user_preferences (profile_id, key, value_json)
                VALUES (?, 'reminders', 'false')
                """,
                (profile_id,),
            )
            old_token = self._session_token()
            if old_token:
                connection.execute("DELETE FROM sessions WHERE token = ?", (old_token,))
            connection.execute(
                "INSERT INTO sessions (token, profile_id) VALUES (?, ?)",
                (token, profile_id),
            )
        self._send_json(200, {"fullName": clean_name, "created": created}, {
            "Set-Cookie": self._session_cookie(token),
        })

    def _handle_api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/demo-profile":
            self._handle_demo_profile(method)
            return
        if path == "/api/logout":
            if method != "POST":
                raise ApiError(405, "Method is not allowed.")
            token = self._session_token()
            if token:
                with connect_database() as connection:
                    connection.execute("DELETE FROM sessions WHERE token = ?", (token,))
            self._send_json(200, {"signedOut": True}, {
                "Set-Cookie": self._session_cookie("", clear=True),
            })
            return

        profile_id = self._authenticated_profile()["id"]
        if path == "/api/appointments":
            if method == "GET":
                with connect_database() as connection:
                    rows = connection.execute(
                        """
                        SELECT data_json FROM appointments
                        WHERE profile_id = ?
                        ORDER BY json_extract(data_json, '$.date'), json_extract(data_json, '$.time')
                        """,
                        (profile_id,),
                    ).fetchall()
                self._send_json(200, [json.loads(row["data_json"]) for row in rows])
                return
            if method == "POST":
                appointment = self._appointment(self._read_json())
                try:
                    with connect_database() as connection:
                        connection.execute(
                            "INSERT INTO appointments (id, status, doctor, data_json, profile_id) VALUES (?, ?, ?, ?, ?)",
                            (
                                appointment["id"],
                                appointment["status"],
                                appointment["doctor"],
                                json.dumps(appointment, ensure_ascii=False),
                                profile_id,
                            ),
                        )
                except sqlite3.IntegrityError as error:
                    raise ApiError(409, "An appointment with this id already exists.") from error
                self._send_json(201, appointment)
                return
        if path.startswith("/api/appointments/") and method == "PUT":
            appointment_id = unquote(path.removeprefix("/api/appointments/"))
            appointment = self._appointment(self._read_json())
            if appointment["id"] != appointment_id:
                raise ApiError(400, "Appointment id in the URL does not match the request body.")
            with connect_database() as connection:
                cursor = connection.execute(
                    """
                    UPDATE appointments
                    SET status = ?, doctor = ?, data_json = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND profile_id = ?
                    """,
                    (
                        appointment["status"],
                        appointment["doctor"],
                        json.dumps(appointment, ensure_ascii=False),
                        appointment_id,
                        profile_id,
                    ),
                )
            if cursor.rowcount == 0:
                raise ApiError(404, "Appointment was not found.")
            self._send_json(200, appointment)
            return
        if path == "/api/preferences":
            if method == "GET":
                with connect_database() as connection:
                    row = connection.execute(
                        "SELECT value_json FROM user_preferences WHERE profile_id = ? AND key = 'reminders'",
                        (profile_id,),
                    ).fetchone()
                self._send_json(200, {"reminders": json.loads(row["value_json"]) if row else False})
                return
            if method == "PUT":
                value = self._read_json().get("reminders")
                if not isinstance(value, bool):
                    raise ApiError(400, "The reminders preference must be true or false.")
                with connect_database() as connection:
                    connection.execute(
                        """
                        INSERT INTO user_preferences (profile_id, key, value_json) VALUES (?, 'reminders', ?)
                        ON CONFLICT(profile_id, key) DO UPDATE
                        SET value_json = excluded.value_json, updated_at = CURRENT_TIMESTAMP
                        """,
                        (profile_id, json.dumps(value)),
                    )
                self._send_json(200, {"reminders": value})
                return
        if path == "/api/medication-checks":
            if method == "GET":
                check_date = query.get("date", [""])[0]
                if not check_date:
                    raise ApiError(400, "A date query parameter is required.")
                with connect_database() as connection:
                    rows = connection.execute(
                        """
                        SELECT medicine_id FROM user_medication_checks
                        WHERE profile_id = ? AND check_date = ? AND taken = 1
                        """,
                        (profile_id, check_date),
                    ).fetchall()
                self._send_json(200, {"date": check_date, "taken": [row["medicine_id"] for row in rows]})
                return
            if method == "PUT":
                value = self._read_json()
                check_date = value.get("date")
                medicine_id = value.get("medicineId")
                taken = value.get("taken")
                if not isinstance(check_date, str) or not check_date or not isinstance(medicine_id, str) or not medicine_id:
                    raise ApiError(400, "A date and medicineId are required.")
                if not isinstance(taken, bool):
                    raise ApiError(400, "The taken value must be true or false.")
                with connect_database() as connection:
                    connection.execute(
                        """
                        INSERT INTO user_medication_checks (profile_id, check_date, medicine_id, taken)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(profile_id, check_date, medicine_id) DO UPDATE
                        SET taken = excluded.taken, updated_at = CURRENT_TIMESTAMP
                        """,
                        (profile_id, check_date, medicine_id, int(taken)),
                    )
                self._send_json(200, {"date": check_date, "medicineId": medicine_id, "taken": taken})
                return
        raise ApiError(404, "API route was not found.")

    def _serve_static(self, path: str) -> None:
        relative_path = "index.html" if path in ("", "/", "/index.html") else None
        if relative_path is None:
            self._send_json(404, {"error": "File was not found."})
            return
        requested = ROOT / relative_path
        content_type = "text/html; charset=utf-8" if requested.suffix == ".html" else "text/plain; charset=utf-8"
        payload = requested.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(payload)

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/"):
                self._handle_api(method, parsed.path, parse_qs(parsed.query))
            elif method == "GET":
                self._serve_static(parsed.path)
            else:
                raise ApiError(405, "Method is not allowed.")
        except ApiError as error:
            self._send_json(error.status, {"error": str(error)})
        except sqlite3.Error:
            self._send_json(500, {"error": "The local database operation failed."})
        except OSError:
            self._send_json(500, {"error": "The local file operation failed."})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def log_message(self, format: str, *args: object) -> None:
        # Avoid writing request content or patient-entered values to server logs.
        super().log_message(format, *args)


def main() -> None:
    initialize_database()
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("KINDRED_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), RequestHandler)
    print(f"Kindred Care demo is available at http://127.0.0.1:{port}")
    print(f"SQLite database: {DATABASE_PATH}")
    print("Demo only. Do not enter real patient information.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local demo server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
