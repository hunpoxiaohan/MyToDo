from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    color TEXT,
    sort_order INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    note TEXT,
    owner_id TEXT REFERENCES people(id),
    due_time TIMESTAMP,
    importance INTEGER DEFAULT 3,
    status TEXT DEFAULT 'todo',
    recurrence TEXT,
    recurrence_group_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    reminder_offset INTEGER DEFAULT 15,
    last_notified_at TIMESTAMP,
    postponed_until TIMESTAMP
);

CREATE TABLE IF NOT EXISTS focus_sessions (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    duration_minutes INTEGER,
    started_at TIMESTAMP,
    completed BOOLEAN
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_time);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id);
CREATE INDEX IF NOT EXISTS idx_tasks_group ON tasks(recurrence_group_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_notified ON tasks(last_notified_at);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    """每个线程各开一条连接：sqlite3 对象跨线程共享是崩溃源。"""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def make_conn_factory(db_path: Path | str):
    """返回 callable，repository 每次操作用它拿短生命周期连接。"""
    return lambda: connect(db_path)


def init_db(conn: sqlite3.Connection) -> int:
    cur = conn.execute("PRAGMA user_version")
    version = cur.fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    return SCHEMA_VERSION
