"""
SQLite persistence layer for ScrapeBot.

Separate project from MailForge - no shared code, no shared database,
no shared naming. This is a standalone scraping/automation web app.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "scrapebot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    name TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,              -- 'config' or 'hn'
    config_path TEXT,                -- NULL for 'hn'
    schedule_minutes INTEGER DEFAULT 0,  -- 0 = manual only, no auto-run
    last_run TEXT,
    last_status TEXT,
    last_row_count INTEGER DEFAULT 0,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    status TEXT NOT NULL,             -- 'success' or 'failed'
    error TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    job_name TEXT NOT NULL,
    data TEXT NOT NULL,               -- JSON blob of the scraped row
    FOREIGN KEY (run_id) REFERENCES runs (id)
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    name TEXT,
    email TEXT,
    phone TEXT,
    company TEXT,
    source_url TEXT,
    notes TEXT,
    extracted_at TEXT NOT NULL
);
"""

DEFAULT_JOBS = [
    ("github_trending", "GitHub Trending", "config", "configs/github_trending.json"),
    ("books_toscrape", "Books to Scrape", "config", "configs/books_toscrape.json"),
    ("quotes_toscrape", "Quotes to Scrape", "config", "configs/example.json"),
    ("hn", "Hacker News Front Page", "hn", None),
]


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)
        for name, label, kind, config_path in DEFAULT_JOBS:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (name, label, kind, config_path) VALUES (?, ?, ?, ?)",
                (name, label, kind, config_path),
            )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_jobs() -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY name").fetchall()


def get_job(job_name: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute("SELECT * FROM jobs WHERE name = ?", (job_name,)).fetchone()


def set_schedule(job_name: str, minutes: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE jobs SET schedule_minutes = ? WHERE name = ?", (minutes, job_name)
        )


def record_run_start() -> None:
    pass  # placeholder for symmetry / future use


def record_run_result(
    job_name: str, rows: list[dict], status: str, error: str | None = None
) -> int:
    ts = now_iso()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO runs (job_name, timestamp, row_count, status, error) VALUES (?, ?, ?, ?, ?)",
            (job_name, ts, len(rows), status, error),
        )
        run_id = cur.lastrowid
        for row in rows:
            conn.execute(
                "INSERT INTO items (run_id, job_name, data) VALUES (?, ?, ?)",
                (run_id, job_name, json.dumps(row, ensure_ascii=False)),
            )
        conn.execute(
            "UPDATE jobs SET last_run = ?, last_status = ?, last_row_count = ?, last_error = ? "
            "WHERE name = ?",
            (ts, status, len(rows), error, job_name),
        )
        return run_id


def latest_items(job_name: str, limit: int = 200) -> list[dict]:
    with get_db() as conn:
        run = conn.execute(
            "SELECT id FROM runs WHERE job_name = ? AND status = 'success' "
            "ORDER BY id DESC LIMIT 1",
            (job_name,),
        ).fetchone()
        if not run:
            return []
        rows = conn.execute(
            "SELECT data FROM items WHERE run_id = ? LIMIT ?", (run["id"], limit)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]


def save_leads(job_name: str, leads: list[dict]) -> None:
    ts = now_iso()
    with get_db() as conn:
        # Replace previous leads for this job with the fresh extraction
        conn.execute("DELETE FROM leads WHERE job_name = ?", (job_name,))
        for lead in leads:
            conn.execute(
                "INSERT INTO leads (job_name, name, email, phone, company, source_url, notes, extracted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_name,
                    lead.get("name", ""),
                    lead.get("email", ""),
                    lead.get("phone", ""),
                    lead.get("company", ""),
                    lead.get("source_url", ""),
                    lead.get("notes", ""),
                    ts,
                ),
            )


def get_leads(job_name: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name, email, phone, company, source_url, notes FROM leads WHERE job_name = ?",
            (job_name,),
        ).fetchall()
        return [dict(r) for r in rows]


def recent_runs(job_name: str, limit: int = 10) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE job_name = ? ORDER BY id DESC LIMIT ?",
            (job_name, limit),
        ).fetchall()
