from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 5

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
    parent_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
    due_time TIMESTAMP,
    importance INTEGER DEFAULT 3,
    status TEXT DEFAULT 'todo',
    recurrence TEXT,
    recurrence_group_id TEXT,
    recurrence_anchor TEXT DEFAULT 'schedule',
    recurrence_until TIMESTAMP,
    repeat_reminder_daily INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    reminder_offset INTEGER DEFAULT 15,
    last_notified_at TIMESTAMP,
    postponed_until TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_notes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS focus_sessions (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    duration_minutes INTEGER,
    started_at TIMESTAMP,
    completed BOOLEAN
);

-- 调度线程要记住的零碎状态（今天推过早报了没）。写它的是扫描线程，
-- config.json 归 GUI 线程管，两边各写各的才不会互相覆盖。
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_time);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id);
CREATE INDEX IF NOT EXISTS idx_tasks_group ON tasks(recurrence_group_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_notified ON tasks(last_notified_at);
CREATE INDEX IF NOT EXISTS idx_task_notes_task ON task_notes(task_id, created_at);
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


# v2→v3：周期任务的锚点 / 截止日 / 每日重复提醒
_TASK_V3_COLUMNS = {
    "recurrence_anchor": "TEXT DEFAULT 'schedule'",
    "recurrence_until": "TIMESTAMP",
    "repeat_reminder_daily": "INTEGER DEFAULT 0",
}

# v4→v5：子任务。ALTER 不允许带 REFERENCES，所以级联删除由 TaskRepository.delete 显式做。
_TASK_V5_COLUMNS = {"parent_id": "TEXT"}


def _add_missing_columns(conn: sqlite3.Connection, table: str, wanted: dict[str, str]) -> None:
    """SCHEMA 全是 IF NOT EXISTS，只建新表、不会给已存在的表补列，所以老库要显式 ALTER。"""
    have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in wanted.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def init_db(conn: sqlite3.Connection) -> int:
    cur = conn.execute("PRAGMA user_version")
    version = cur.fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(SCHEMA)
        if version < 3:
            _add_missing_columns(conn, "tasks", _TASK_V3_COLUMNS)
        if version < 5:
            _add_missing_columns(conn, "tasks", _TASK_V5_COLUMNS)
        # 建在补列之后：老库的 tasks 还没有 parent_id，SCHEMA 里一起写会直接报错
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    return SCHEMA_VERSION
