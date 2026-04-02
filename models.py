from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ApprovalRepository:
    def __init__(self, database_url: str) -> None:
        self.db_path = self._parse_database_url(database_url)

    def _parse_database_url(self, database_url: str) -> Path:
        prefix = "sqlite:///"
        if database_url.startswith(prefix):
            raw_path = database_url[len(prefix):]
            return Path(raw_path)

        if database_url.startswith("postgres://") or database_url.startswith("postgresql://"):
            raise ValueError(
                "This deployment is configured for SQLite on the Render persistent disk. "
                "Use a sqlite:/// DATABASE_URL."
            )

        return Path(database_url)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    technician_name TEXT NOT NULL,
                    job_number TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    service_address TEXT NOT NULL,
                    city_state_zip TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    pdf_filename TEXT NOT NULL,
                    original_screenshot_filename TEXT,
                    extraction_json TEXT,
                    status TEXT NOT NULL DEFAULT 'generated'
                )
                """
            )
            conn.commit()
            self._ensure_column(conn, "approvals", "original_screenshot_filename", "TEXT")
            self._ensure_column(conn, "approvals", "extraction_json", "TEXT")
            self._ensure_column(conn, "approvals", "status", "TEXT NOT NULL DEFAULT 'generated'")

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_sql: str,
    ) -> None:
        columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {row["name"] for row in columns}
        if column_name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
            conn.commit()

    def create_approval(
        self,
        technician_name: str,
        job_number: str,
        customer_name: str,
        service_address: str,
        city_state_zip: str,
        phone_number: str,
        pdf_filename: str,
        original_screenshot_filename: str | None,
        extraction_json: str,
        status: str,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO approvals (
                    technician_name,
                    job_number,
                    customer_name,
                    service_address,
                    city_state_zip,
                    phone_number,
                    pdf_filename,
                    original_screenshot_filename,
                    extraction_json,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    technician_name,
                    job_number,
                    customer_name,
                    service_address,
                    city_state_zip,
                    phone_number,
                    pdf_filename,
                    original_screenshot_filename,
                    extraction_json,
                    status,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def get_approval(self, approval_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    created_at,
                    technician_name,
                    job_number,
                    customer_name,
                    service_address,
                    city_state_zip,
                    phone_number,
                    pdf_filename,
                    original_screenshot_filename,
                    extraction_json,
                    status
                FROM approvals
                WHERE id = ?
                """,
                (approval_id,),
            ).fetchone()

        if row is None:
            return None

        return dict(row)
