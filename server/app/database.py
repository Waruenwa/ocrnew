from __future__ import annotations

import sqlite3

from app.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                mime_type TEXT,
                status TEXT NOT NULL,
                total_pages INTEGER NOT NULL,
                processed_pages INTEGER NOT NULL DEFAULT 0,
                extraction_prompt TEXT,
                ocr_markdown TEXT,
                structured_output TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS job_pages (
                job_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                markdown TEXT NOT NULL,
                segments_json TEXT,
                PRIMARY KEY (job_id, page_number),
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            )
            """
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(job_pages)").fetchall()
        }
        if "segments_json" not in columns:
            connection.execute("ALTER TABLE job_pages ADD COLUMN segments_json TEXT")
        connection.commit()
