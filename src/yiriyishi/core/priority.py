from __future__ import annotations

from datetime import datetime
from enum import Enum

from .models import PrioritySettings, STATUS_DONE, Task

# 颜色 token 由 ui/theme.py 映射到具体 hex（深浅模式不同），这里只给语义键。


class Level(str, Enum):
    OVERDUE = "overdue"
    URGENT = "urgent"
    WARNING = "warning"
    TODAY = "today"
    NEAR = "near"
    WEEK = "week"
    FUTURE = "future"
    NORMAL = "normal"
    DONE = "done"


class Bucket(str, Enum):
    """任务分类：逾期 / 今日 / 周期 / 未来 / 已完成。"""

    OVERDUE = "overdue"
    TODAY = "today"
    RECURRING = "recurring"
    FUTURE = "future"
    DONE = "done"
    NONE = "none"


BUCKET_LABELS = {
    Bucket.OVERDUE: "逾期",
    Bucket.TODAY: "今日",
    Bucket.RECURRING: "周期",
    Bucket.FUTURE: "未来",
    Bucket.DONE: "已完成",
    Bucket.NONE: "未排期",
}


class PriorityResult:
    __slots__ = ("level", "score", "minutes_left")

    def __init__(self, level: Level, score: float, minutes_left: float | None):
        self.level = level
        self.score = score
        self.minutes_left = minutes_left

    def __repr__(self) -> str:
        return f"PriorityResult({self.level.value}, score={self.score:.1f}, left={self.minutes_left})"

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, PriorityResult)
            and other.level == self.level
            and abs(other.score - self.score) < 1e-6
        )


def minutes_left(due: datetime, now: datetime) -> float:
    return (due - now).total_seconds() / 60.0


def compute_priority(
    task: Task,
    now: datetime,
    settings: PrioritySettings | None = None,
) -> PriorityResult:
    cfg = settings or PrioritySettings()

    if task.status == STATUS_DONE:
        return PriorityResult(Level.DONE, -1000.0, None)

    due = task.effective_due()
    if due is None:
        factor = 0.0 if task.importance <= 3 else 8.0
        return PriorityResult(Level.NORMAL, task.importance * 10 + factor, None)

    m = minutes_left(due, now)
    urgent = float(cfg.urgent_minutes)
    warning = float(cfg.warning_hours * 60)

    if m < 0:
        level, factor = Level.OVERDUE, 100 + min(20.0, -m / 60.0)
    elif due.date() == now.date():
        # 当天任务：按小时尺度
        if m <= urgent:
            level, factor = Level.URGENT, 70 + (urgent - m) / max(urgent, 1e-9) * 20
        elif m <= warning:
            span = max(warning - urgent, 1e-9)
            level, factor = Level.WARNING, 45 + (warning - m) / span * 25
        else:
            level, factor = Level.TODAY, 30 + max(0.0, (1 - m / 1440.0)) * 15
    else:
        # 跨天任务：按天尺度
        if m <= cfg.near_days * 1440:
            level, factor = Level.NEAR, 25.0
        elif m <= cfg.week_days * 1440:
            level, factor = Level.WEEK, 15.0
        else:
            level, factor = Level.FUTURE, 5.0

    return PriorityResult(level, task.importance * 10 + factor, m)


def bucket_of(task: Task, now: datetime) -> Bucket:
    if task.status == STATUS_DONE:
        return Bucket.DONE
    if task.recurrence:
        return Bucket.RECURRING
    due = task.effective_due()
    if due is None:
        return Bucket.NONE
    if due < now:
        return Bucket.OVERDUE
    if due.date() == now.date():
        return Bucket.TODAY
    return Bucket.FUTURE


_SORT_KEY = {
    "priority": lambda item: (-item[1].score, item[0].effective_due() or datetime.max, item[0].created_at),
    "due": lambda item: (item[0].effective_due() or datetime.max, -item[1].score),
    "created": lambda item: (-item[0].created_at.timestamp(),),
}


def sort_tasks(tasks: list[Task], now: datetime, mode: str = "priority",
               settings: PrioritySettings | None = None) -> list[Task]:
    """按 auto_priority_score 降序；done 沉底。mode 非法时退回 priority。"""
    cfg = settings or PrioritySettings()
    pairs = [(t, compute_priority(t, now, cfg)) for t in tasks]
    key = _SORT_KEY.get(mode, _SORT_KEY["priority"])
    done = [t for t, _ in pairs if t.status == STATUS_DONE]
    open_ = [(t, p) for t, p in pairs if t.status != STATUS_DONE]
    open_.sort(key=key)
    done.sort(key=lambda t: t.completed_at or t.created_at, reverse=True)
    return [t for t, _ in open_] + done


def humanize_left(minutes: float | None) -> str:
    if minutes is None:
        return "未排期"
    total = int(minutes)
    a = abs(total)
    if a < 60:
        tail = f"{a} 分钟"
    elif a < 1440:
        h, mm = divmod(a, 60)
        tail = f"{h} 小时 {mm} 分" if mm else f"{h} 小时"
    else:
        d, rem = divmod(a, 1440)
        h = rem // 60
        tail = f"{d} 天 {h} 小时" if h else f"{d} 天"
    return f"已逾期 {tail}" if total < 0 else f"{tail}后"
