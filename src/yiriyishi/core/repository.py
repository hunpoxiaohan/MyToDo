from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from .models import STATUS_ARCHIVED, STATUS_DONE, STATUS_TODO, Person, Task, fmt_dt

_TASK_COLS = """id, title, note, owner_id, due_time, importance, status, recurrence,
    recurrence_group_id, created_at, completed_at, reminder_offset, last_notified_at,
    postponed_until"""


class _Base:
    """仓库持有一条属于当前线程的连接。跨线程共用连接是崩溃源，
    因此 GUI 线程与调度线程各自 new 一个仓库实例。"""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @contextmanager
    def _tx(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


class TaskRepository(_Base):
    def save(self, task: Task) -> Task:
        if task.recurrence and not task.recurrence_group_id:
            task.recurrence_group_id = task.id
        row = task.to_row()
        with self._tx():
            self._conn.execute(
                f"""INSERT INTO tasks ({_TASK_COLS}) VALUES (
                    :id, :title, :note, :owner_id, :due_time, :importance, :status,
                    :recurrence, :recurrence_group_id, :created_at, :completed_at,
                    :reminder_offset, :last_notified_at, :postponed_until
                )
                ON CONFLICT(id) DO UPDATE SET
                    title=:title, note=:note, owner_id=:owner_id, due_time=:due_time,
                    importance=:importance, status=:status, recurrence=:recurrence,
                    recurrence_group_id=:recurrence_group_id, completed_at=:completed_at,
                    reminder_offset=:reminder_offset, last_notified_at=:last_notified_at,
                    postponed_until=:postponed_until""",
                row,
            )
        return task

    def create(self, **kwargs) -> Task:
        return self.save(Task(**kwargs))

    def get(self, task_id: str) -> Task | None:
        row = self._conn.execute(f"SELECT {_TASK_COLS} FROM tasks WHERE id=?", (task_id,)).fetchone()
        return Task.from_row(row) if row else None

    def delete(self, task_id: str) -> None:
        with self._tx():
            self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def list(self, include_archived: bool = False, collapse_recurring: bool = True) -> list[Task]:
        if include_archived:
            rows = self._conn.execute(f"SELECT {_TASK_COLS} FROM tasks").fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {_TASK_COLS} FROM tasks WHERE status != ?", (STATUS_ARCHIVED,)
            ).fetchall()
        tasks = [Task.from_row(r) for r in rows]
        return _collapse_groups(tasks) if collapse_recurring else tasks

    def list_by(self, status: str | None = None, owner_id: str | None = None) -> list[Task]:
        clauses, args = [], []
        if status:
            clauses.append("status=?")
            args.append(status)
        if owner_id:
            clauses.append("owner_id=?")
            args.append(owner_id)
        sql = f"SELECT {_TASK_COLS} FROM tasks"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [Task.from_row(r) for r in self._conn.execute(sql, args).fetchall()]

    def notification_candidates(self, now: datetime) -> list[Task]:
        """到点未提醒的任务：now >= 到期 - reminder_offset 且 last_notified_at 为空。"""
        rows = self._conn.execute(
            f"""SELECT {_TASK_COLS} FROM tasks
                WHERE status=? AND due_time IS NOT NULL AND last_notified_at IS NULL""",
            (STATUS_TODO,),
        ).fetchall()
        out = []
        for row in rows:
            task = Task.from_row(row)
            due = task.effective_due()
            if due is None:
                continue
            if now >= due - timedelta(minutes=task.reminder_offset):
                out.append(task)
        return out

    def mark_notified(self, task_id: str, when: datetime) -> None:
        with self._tx():
            self._conn.execute(
                "UPDATE tasks SET last_notified_at=? WHERE id=?", (fmt_dt(when), task_id)
            )

    def postpone(self, task_id: str, until: datetime) -> None:
        with self._tx():
            self._conn.execute(
                "UPDATE tasks SET postponed_until=?, last_notified_at=NULL WHERE id=?",
                (fmt_dt(until), task_id),
            )

    def stats(self, now: datetime) -> dict:
        tasks = self.list()
        today = now.date()
        open_tasks = [t for t in tasks if t.status == STATUS_TODO]
        return {
            "open": len(open_tasks),
            "overdue": sum(1 for t in open_tasks if t.effective_due() and t.effective_due() < now),
            "due_today": sum(
                1 for t in open_tasks if t.effective_due() and t.effective_due().date() == today
            ),
            "done_today": sum(
                1
                for t in tasks
                if t.status == STATUS_DONE and t.completed_at and t.completed_at.date() == today
            ),
        }


def _collapse_groups(tasks: list[Task]) -> list[Task]:
    """周期任务同一组只保留未完成的最早一期；已完成的历史期数留在“已完成”。"""
    current: dict[str, Task] = {}
    result: list[Task] = []
    for t in tasks:
        if not t.recurrence or not t.recurrence_group_id or t.status != STATUS_TODO:
            result.append(t)
            continue
        existing = current.get(t.recurrence_group_id)
        if existing is None or (t.due_time or datetime.max) < (existing.due_time or datetime.max):
            current[t.recurrence_group_id] = t
    result.extend(current.values())
    return result


class PersonRepository(_Base):
    def save(self, person: Person) -> Person:
        with self._tx():
            self._conn.execute(
                """INSERT INTO people (id, name, color, sort_order, active)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, color=excluded.color,
                     sort_order=excluded.sort_order, active=excluded.active""",
                (person.id, person.name, person.color, person.sort_order, int(person.active)),
            )
        return person

    def create(self, name: str, color: str | None = None) -> Person:
        name = name.strip()
        existing = self.find_by_name(name)
        if existing:
            if not existing.active:
                self.set_active(existing.id, True)
            return existing
        order = self._conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM people").fetchone()[0]
        return self.save(Person(name=name, color=color, sort_order=order))

    def get(self, person_id: str) -> Person | None:
        row = self._conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
        return _person_from_row(row) if row else None

    def find_by_name(self, name: str) -> Person | None:
        row = self._conn.execute("SELECT * FROM people WHERE name=?", (name.strip(),)).fetchone()
        return _person_from_row(row) if row else None

    def list(self, active_only: bool = True) -> list[Person]:
        sql = "SELECT * FROM people"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY sort_order, name"
        return [_person_from_row(r) for r in self._conn.execute(sql).fetchall()]

    def set_active(self, person_id: str, active: bool) -> None:
        with self._tx():
            self._conn.execute("UPDATE people SET active=? WHERE id=?", (int(active), person_id))

    def delete(self, person_id: str) -> None:
        with self._tx():
            self._conn.execute("UPDATE tasks SET owner_id=NULL WHERE owner_id=?", (person_id,))
            self._conn.execute("DELETE FROM people WHERE id=?", (person_id,))


def _person_from_row(row) -> Person:
    return Person(
        id=row["id"],
        name=row["name"],
        color=row["color"],
        sort_order=row["sort_order"] or 0,
        active=bool(row["active"]),
    )
