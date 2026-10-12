from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime

STATUS_TODO = "todo"
STATUS_DONE = "done"
STATUS_ARCHIVED = "archived"


def new_id() -> str:
    return uuid.uuid4().hex


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace(" ", "T")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromisoformat(text[:19])


def fmt_dt(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


@dataclass
class Person:
    id: str = field(default_factory=new_id)
    name: str = ""
    color: str | None = None
    sort_order: int = 0
    active: bool = True


@dataclass
class Task:
    id: str = field(default_factory=new_id)
    title: str = ""
    note: str = ""
    owner_id: str | None = None
    parent_id: str | None = None             # 只有一层：子任务不再挂子任务
    due_time: datetime | None = None
    importance: int = 3
    status: str = STATUS_TODO
    recurrence: str | None = None
    recurrence_group_id: str | None = None
    recurrence_anchor: str = "schedule"        # schedule=按日程排 / completion=从完成日往后数
    recurrence_until: datetime | None = None   # 只到「直到某日」，当天那一期仍会生成
    repeat_reminder_daily: bool = False        # 没完成也每天到点再提醒（提醒的重复，非任务的重复）
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    reminder_offset: int = 15
    last_notified_at: datetime | None = None
    postponed_until: datetime | None = None
    # 汇总字段：查询时由 TaskRepository 现算并挂上，不落库
    children_due: datetime | None = None
    subtask_done: int = 0
    subtask_total: int = 0

    def scheduled_due(self) -> datetime | None:
        """自身该做的时间：提醒只认它。推迟不改 due_time，但颜色/分类/排序随后移。"""
        if self.postponed_until and (self.due_time is None or self.postponed_until > self.due_time):
            return self.postponed_until
        return self.due_time

    def effective_due(self) -> datetime | None:
        """给优先级、颜色和排序看的时间：父任务要跟着最早那个未完成子任务走，
        否则「下面有件明天到期」会在列表里隐身。"""
        candidates = [d for d in (self.scheduled_due(), self.children_due) if d is not None]
        return min(candidates) if candidates else None

    @property
    def is_recurring(self) -> bool:
        return bool(self.recurrence)

    def copy_for_next_period(self, due_time: datetime) -> Task:
        """新一期是顶层任务：子任务由滚期逻辑按偏移显式复制，不跟着一起搬。"""
        return Task(
            title=self.title,
            note=self.note,
            owner_id=self.owner_id,
            due_time=due_time,
            importance=self.importance,
            status=STATUS_TODO,
            recurrence=self.recurrence,
            recurrence_group_id=self.recurrence_group_id or self.id,
            recurrence_anchor=self.recurrence_anchor,
            recurrence_until=self.recurrence_until,
            repeat_reminder_daily=self.repeat_reminder_daily,
            reminder_offset=self.reminder_offset,
        )

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "note": self.note,
            "owner_id": self.owner_id,
            "parent_id": self.parent_id,
            "due_time": fmt_dt(self.due_time),
            "importance": self.importance,
            "status": self.status,
            "recurrence": self.recurrence,
            "recurrence_group_id": self.recurrence_group_id,
            "recurrence_anchor": self.recurrence_anchor,
            "recurrence_until": fmt_dt(self.recurrence_until),
            "repeat_reminder_daily": int(self.repeat_reminder_daily),
            "created_at": fmt_dt(self.created_at) or datetime.now().isoformat(timespec="seconds"),
            "completed_at": fmt_dt(self.completed_at),
            "reminder_offset": self.reminder_offset,
            "last_notified_at": fmt_dt(self.last_notified_at),
            "postponed_until": fmt_dt(self.postponed_until),
        }

    @classmethod
    def from_row(cls, row) -> Task:
        return cls(
            id=row["id"],
            title=row["title"],
            note=row["note"] or "",
            owner_id=row["owner_id"],
            parent_id=row["parent_id"],
            due_time=parse_dt(row["due_time"]),
            importance=row["importance"] if row["importance"] is not None else 3,
            status=row["status"] or STATUS_TODO,
            recurrence=row["recurrence"],
            recurrence_group_id=row["recurrence_group_id"],
            recurrence_anchor=row["recurrence_anchor"] or "schedule",
            recurrence_until=parse_dt(row["recurrence_until"]),
            repeat_reminder_daily=bool(row["repeat_reminder_daily"]),
            created_at=parse_dt(row["created_at"]) or datetime.now(),
            completed_at=parse_dt(row["completed_at"]),
            reminder_offset=row["reminder_offset"] if row["reminder_offset"] is not None else 15,
            last_notified_at=parse_dt(row["last_notified_at"]),
            postponed_until=parse_dt(row["postponed_until"]),
        )

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "note": self.note,
            "owner_id": self.owner_id,
            "due_time": fmt_dt(self.due_time),
            "importance": self.importance,
            "status": self.status,
            "recurrence": self.recurrence,
            "recurrence_group_id": self.recurrence_group_id,
            "created_at": fmt_dt(self.created_at),
            "completed_at": fmt_dt(self.completed_at),
            "reminder_offset": self.reminder_offset,
        }


@dataclass
class TaskNote:
    """任务的过程记录：一条 = 一个带时间戳的追加条目，和 task.note（静态备注）是两回事。"""

    id: str = field(default_factory=new_id)
    task_id: str = ""
    body: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime | None = None

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "body": self.body,
            "created_at": fmt_dt(self.created_at),
            "updated_at": fmt_dt(self.updated_at),
        }

    @classmethod
    def from_row(cls, row) -> TaskNote:
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            body=row["body"] or "",
            created_at=parse_dt(row["created_at"]) or datetime.now(),
            updated_at=parse_dt(row["updated_at"]),
        )


@dataclass
class PrioritySettings:
    """阈值全部可在设置页改。"""

    urgent_minutes: int = 30      # 当天任务：剩余 ≤ 30min 视为紧急（红）
    warning_hours: int = 8        # 当天任务：剩余 ≤ 8h 视为临近（黄）
    near_days: int = 1            # 跨天任务：剩余 ≤ 1 天（黄）
    week_days: int = 3            # 跨天任务：剩余 ≤ 3 天（浅绿）


@dataclass
class AppSettings:
    theme_mode: str = "system"           # system / light / dark
    accent: str = "#8A9A5B"
    dnd_enabled: bool = True
    dnd_start: str = "22:00"
    dnd_end: str = "08:00"
    morning_review_enabled: bool = True
    morning_review_time: str = "08:30"
    priority: PrioritySettings = field(default_factory=PrioritySettings)
    autostart_intent: bool = False
    start_minimized: bool = False
    reminders_paused: bool = False
    float_ball_enabled: bool = True
    float_ball_pos: tuple[int, int] | None = None
    window_geometry: dict | None = None
    selected_task_id: str | None = None
    default_sort: str = "priority"       # priority / due / created / owner
    backup_dir: str | None = None
    last_backup_ok: bool | None = None
    schema_version: int = 1

    def to_dict(self) -> dict:
        return {
            "theme_mode": self.theme_mode,
            "accent": self.accent,
            "dnd_enabled": self.dnd_enabled,
            "dnd_start": self.dnd_start,
            "dnd_end": self.dnd_end,
            "morning_review_enabled": self.morning_review_enabled,
            "morning_review_time": self.morning_review_time,
            "priority": {
                "urgent_minutes": self.priority.urgent_minutes,
                "warning_hours": self.priority.warning_hours,
                "near_days": self.priority.near_days,
                "week_days": self.priority.week_days,
            },
            "autostart_intent": self.autostart_intent,
            "start_minimized": self.start_minimized,
            "reminders_paused": self.reminders_paused,
            "float_ball_enabled": self.float_ball_enabled,
            "float_ball_pos": list(self.float_ball_pos) if self.float_ball_pos else None,
            "window_geometry": self.window_geometry,
            "selected_task_id": self.selected_task_id,
            "default_sort": self.default_sort,
            "backup_dir": self.backup_dir,
            "last_backup_ok": self.last_backup_ok,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AppSettings:
        s = cls()
        p = data.get("priority") or {}
        s.priority = PrioritySettings(
            urgent_minutes=int(p.get("urgent_minutes", s.priority.urgent_minutes)),
            warning_hours=int(p.get("warning_hours", s.priority.warning_hours)),
            near_days=int(p.get("near_days", s.priority.near_days)),
            week_days=int(p.get("week_days", s.priority.week_days)),
        )
        for key in (
            "theme_mode", "accent", "dnd_start", "dnd_end", "default_sort",
            "selected_task_id", "backup_dir", "autostart_intent", "start_minimized",
            "reminders_paused", "float_ball_enabled", "dnd_enabled", "last_backup_ok",
            "morning_review_enabled", "morning_review_time",
        ):
            if key in data and data[key] is not None:
                setattr(s, key, data[key])
        if data.get("window_geometry"):
            s.window_geometry = dict(data["window_geometry"])
        if data.get("float_ball_pos"):
            s.float_ball_pos = (int(data["float_ball_pos"][0]), int(data["float_ball_pos"][1]))
        s.schema_version = int(data.get("schema_version", s.schema_version))
        return s


def is_in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    """免打扰时段，支持跨午夜；start == end 视为不限制。"""
    if start == end:
        return False
    t: date = now.date()
    cur = now.time().replace(microsecond=0)
    s = _parse_hm(start)
    e = _parse_hm(end)
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e


def _parse_hm(text: str) -> dtime:
    hh, mm = text.split(":")
    return dtime(hour=int(hh), minute=int(mm))
