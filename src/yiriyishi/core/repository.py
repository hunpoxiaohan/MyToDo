from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from .models import STATUS_ARCHIVED, STATUS_DONE, STATUS_TODO, Person, Task, TaskNote, fmt_dt
from .recurrence import scheduled_on

_TASK_COLS = """id, title, note, owner_id, parent_id, due_time, importance, status, recurrence,
    recurrence_group_id, recurrence_anchor, recurrence_until, repeat_reminder_daily,
    created_at, completed_at, reminder_offset, last_notified_at, postponed_until"""

_NOTE_COLS = "id, task_id, body, created_at, updated_at"


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
                    :id, :title, :note, :owner_id, :parent_id, :due_time, :importance, :status,
                    :recurrence, :recurrence_group_id, :recurrence_anchor, :recurrence_until,
                    :repeat_reminder_daily, :created_at, :completed_at, :reminder_offset,
                    :last_notified_at, :postponed_until
                )
                ON CONFLICT(id) DO UPDATE SET
                    title=:title, note=:note, owner_id=:owner_id, parent_id=:parent_id,
                    due_time=:due_time,
                    importance=:importance, status=:status, recurrence=:recurrence,
                    recurrence_group_id=:recurrence_group_id,
                    recurrence_anchor=:recurrence_anchor, recurrence_until=:recurrence_until,
                    repeat_reminder_daily=:repeat_reminder_daily, completed_at=:completed_at,
                    reminder_offset=:reminder_offset, last_notified_at=:last_notified_at,
                    postponed_until=:postponed_until""",
                row,
            )
        return task

    def create(self, **kwargs) -> Task:
        return self.save(Task(**kwargs))

    def get(self, task_id: str) -> Task | None:
        row = self._conn.execute(f"SELECT {_TASK_COLS} FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._attach_rollups([Task.from_row(row)])[0] if row else None

    def delete(self, task_id: str) -> None:
        with self._tx():
            # 老库的 parent_id 是 ALTER 加的、带不上 REFERENCES，级联只能显式删
            self._conn.execute("DELETE FROM tasks WHERE parent_id=?", (task_id,))
            self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def list(self, include_archived: bool = False, collapse_recurring: bool = True) -> list[Task]:
        """主列表只给顶层任务：子任务挤进「今天做哪一件」会把它拆碎。"""
        clauses, args = ["parent_id IS NULL"], []
        if not include_archived:
            clauses.append("status != ?")
            args.append(STATUS_ARCHIVED)
        rows = self._conn.execute(
            f"SELECT {_TASK_COLS} FROM tasks WHERE " + " AND ".join(clauses), args
        ).fetchall()
        tasks = self._attach_rollups([Task.from_row(r) for r in rows])
        return _collapse_groups(tasks) if collapse_recurring else tasks

    def subtasks(self, parent_id: str) -> list[Task]:
        """子任务：有截止时间的排前面，没有的按创建顺序垫底。"""
        rows = self._conn.execute(
            f"SELECT {_TASK_COLS} FROM tasks WHERE parent_id=?"
            " ORDER BY due_time IS NULL, due_time, created_at, rowid",
            (parent_id,),
        ).fetchall()
        return [Task.from_row(r) for r in rows]

    def subtask_counts(self) -> dict[str, tuple[int, int]]:
        """{父 id: (已完成, 总数)}，不含已归档的子任务 —— 列表徽标用。"""
        counts: dict[str, list[int]] = {}
        for child in self._children():
            entry = counts.setdefault(child.parent_id, [0, 0])
            entry[1] += 1
            if child.status == STATUS_DONE:
                entry[0] += 1
        return {parent: (done, total) for parent, (done, total) in counts.items()}

    def _children(self) -> list[Task]:
        rows = self._conn.execute(
            f"SELECT {_TASK_COLS} FROM tasks WHERE parent_id IS NOT NULL AND status != ?",
            (STATUS_ARCHIVED,),
        ).fetchall()
        return [Task.from_row(r) for r in rows]

    def _attach_rollups(self, tasks: list[Task]) -> list[Task]:
        """把子任务的汇总挂到父任务上：颜色/排序跟着最早那个未完成子任务走，
        但提醒只认父任务自己的日子（子任务会自己响）。"""
        if not tasks:
            return tasks
        rollups: dict[str, list] = {}
        for child in self._children():
            entry = rollups.setdefault(child.parent_id, [None, 0, 0])
            entry[2] += 1
            if child.status == STATUS_DONE:
                entry[1] += 1
            elif child.scheduled_due() is not None:
                if entry[0] is None or child.scheduled_due() < entry[0]:
                    entry[0] = child.scheduled_due()
        for task in tasks:
            entry = rollups.get(task.id)
            if entry:
                task.children_due, task.subtask_done, task.subtask_total = entry
        return tasks

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
        """到点该提醒的任务。普通任务每期只提醒一次（靠 last_notified_at）；
        开了「没完成也每天提醒」的周期任务按天去重 —— 重复的是提醒，不是任务。"""
        rows = self._conn.execute(
            f"""SELECT {_TASK_COLS} FROM tasks
                WHERE status=? AND due_time IS NOT NULL""",
            (STATUS_TODO,),
        ).fetchall()
        out = []
        for row in rows:
            task = Task.from_row(row)
            if task.repeat_reminder_daily and task.is_recurring:
                if self._daily_refire_due(task, now):
                    out.append(task)
                continue
            due = task.scheduled_due()
            if due is None:
                continue
            if now < due - timedelta(minutes=task.reminder_offset):
                continue
            if task.last_notified_at is not None:
                continue
            out.append(task)
        return out

    @staticmethod
    def _daily_refire_due(task: Task, now: datetime) -> bool:
        if not scheduled_on(task.due_time, task.recurrence, now.date()):
            return False
        fire_at = datetime.combine(now.date(), task.due_time.time()) - timedelta(
            minutes=task.reminder_offset
        )
        # 手动「稍后再提醒」会把下一次可提醒时刻往后推，否则刚按下顺延、
        # 30 秒后的下一轮扫描又追上来。
        if now < max(fire_at, task.scheduled_due()):
            return False
        return task.last_notified_at is None or task.last_notified_at.date() < now.date()

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


class NoteRepository(_Base):
    """任务过程记录。列表按写入顺序返回（最旧在前），分组与倒序是界面层的事。"""

    def add(self, task_id: str, body: str, when: datetime | None = None) -> TaskNote:
        # 秒精度：入库走 fmt_dt 会截掉微秒，不截的话 add() 返回的对象和库里读回来的不一样
        note = TaskNote(task_id=task_id, body=body,
                        created_at=(when or datetime.now()).replace(microsecond=0))
        row = note.to_row()
        with self._tx():
            self._conn.execute(
                """INSERT INTO task_notes (id, task_id, body, created_at, updated_at)
                   VALUES (:id, :task_id, :body, :created_at, :updated_at)""",
                row,
            )
        return note

    def get(self, note_id: str) -> TaskNote | None:
        row = self._conn.execute(
            f"SELECT {_NOTE_COLS} FROM task_notes WHERE id=?", (note_id,)
        ).fetchone()
        return TaskNote.from_row(row) if row else None

    def list(self, task_id: str) -> list[TaskNote]:
        rows = self._conn.execute(
            f"SELECT {_NOTE_COLS} FROM task_notes WHERE task_id=?"
            " ORDER BY created_at, rowid",
            (task_id,),
        ).fetchall()
        return [TaskNote.from_row(r) for r in rows]

    def update(self, note_id: str, body: str, when: datetime | None = None) -> TaskNote | None:
        with self._tx():
            self._conn.execute(
                "UPDATE task_notes SET body=?, updated_at=? WHERE id=?",
                (body, fmt_dt(when or datetime.now()), note_id),
            )
        return self.get(note_id)

    def delete(self, note_id: str) -> None:
        with self._tx():
            self._conn.execute("DELETE FROM task_notes WHERE id=?", (note_id,))


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


class MetaRepository(_Base):
    """调度线程要记住的零碎状态（今天推过早报了没）。"""

    def get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        with self._tx():
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )


def _person_from_row(row) -> Person:
    return Person(
        id=row["id"],
        name=row["name"],
        color=row["color"],
        sort_order=row["sort_order"] or 0,
        active=bool(row["active"]),
    )
