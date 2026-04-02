# models.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import psycopg2
from psycopg2.extras import RealDictCursor


class ApprovalRepository:
    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is required.")
        self.database_url = self._normalize_database_url(database_url)

    def initialize(self) -> None:
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    id SERIAL PRIMARY KEY,
                    technician_name TEXT NOT NULL,
                    job_number TEXT NOT NULL,
                    customer_name TEXT NOT NULL,
                    service_address TEXT NOT NULL,
                    city_state_zip TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    pdf_filename TEXT NOT NULL,
                    original_screenshot_filename TEXT,
                    extraction_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                    document_json JSONB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'generated',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

    def create_approval(
        self,
        *,
        technician_name: str,
        job_number: str,
        customer_name: str,
        service_address: str,
        city_state_zip: str,
        phone_number: str,
        pdf_filename: str,
        original_screenshot_filename: str | None,
        extraction_json: str,
        document_json: str,
        status: str,
    ) -> int:
        with psycopg2.connect(self.database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
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
                    document_json,
                    status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                RETURNING id
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
                    document_json,
                    status,
                ),
            )
            return int(cursor.fetchone()[0])

    def get_approval(self, approval_id: int) -> dict[str, Any] | None:
        with psycopg2.connect(self.database_url, cursor_factory=RealDictCursor) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    technician_name,
                    job_number,
                    customer_name,
                    service_address,
                    city_state_zip,
                    phone_number,
                    pdf_filename,
                    original_screenshot_filename,
                    extraction_json,
                    document_json,
                    status,
                    created_at
                FROM approvals
                WHERE id = %s
                """,
                (approval_id,),
            )
            record = cursor.fetchone()
            if not record:
                return None

            approval = dict(record)
            approval["extraction_json"] = self._coerce_json(approval.get("extraction_json"))
            approval["document_json"] = self._coerce_json(approval.get("document_json"))
            created_at = approval.get("created_at")
            if isinstance(created_at, datetime):
                approval["created_at"] = created_at.astimezone(timezone.utc)
            return approval

    def _coerce_json(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            return json.loads(value)
        return {}

    def _normalize_database_url(self, database_url: str) -> str:
        if database_url.startswith("postgres://"):
            return "postgresql://" + database_url[len("postgres://") :]

        parsed = urlparse(database_url)
        if parsed.scheme not in {"postgresql", "postgres"}:
            raise ValueError("DATABASE_URL must be a PostgreSQL connection string.")
        return database_url
