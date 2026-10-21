
import sqlite3
from contextlib import contextmanager

from .config import settings


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        settings.database_path,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                original_path TEXT NOT NULL,
                result_path TEXT
            );

            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL
                    REFERENCES jobs(id) ON DELETE CASCADE,
                frame_number INTEGER NOT NULL DEFAULT 0,
                timestamp_seconds REAL NOT NULL DEFAULT 0,
                label TEXT NOT NULL,
                confidence REAL NOT NULL,
                x1 REAL NOT NULL,
                y1 REAL NOT NULL,
                x2 REAL NOT NULL,
                y2 REAL NOT NULL,
                preview_path TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_created_at
                ON jobs(created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_detections_job
                ON detections(job_id);

            CREATE INDEX IF NOT EXISTS idx_detections_label
                ON detections(label);
            """
        )

        # Migration: add track_id to an existing database.
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(detections)"
            ).fetchall()
        }

        if "track_id" not in columns:
            conn.execute(
                "ALTER TABLE detections ADD COLUMN track_id INTEGER"
            )


@contextmanager
def db():
    conn = connect()

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
