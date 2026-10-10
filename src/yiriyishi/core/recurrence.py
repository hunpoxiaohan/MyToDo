from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from .models import STATUS_DONE, Task, new_id
from .repository import TaskRepository


def next_due(current_due: datetime, rule: str) -> datetime | None:
    kind, _, arg = rule.partition(":")
    if kind == "daily":
        return current_due + timedelta(days=1)
    if kind == "every_n_days":
        return current_due + timedelta(days=int(arg))
    if kind == "weekly":
        days = sorted({int(d) for d in arg.split(",") if d.strip()})
        if not days:
            return None
        for step in range(1, 8):
            candidate = current_due + timedelta(days=step)
            if candidate.isoweekday() in days:
                return candidate.replace(hour=current_due.hour, minute=current_due.minute)
        return None
    if kind == "monthly":
        day = int(arg)
        year, month = current_due.year, current_due.month + 1
        if month > 12:
            month, year = 1, year + 1
        clamped = min(day, calendar.monthrange(year, month)[1])
        result = current_due.replace(year=year, month=month, day=clamped)
        return result
    return None


def complete_and_roll(repo: TaskRepository, task: Task, now: datetime) -> Task | None:
    """完成当期；周期任务自动生成下一期。返回新建的下一期任务，或 None。"""
    task.status = STATUS_DONE
    task.completed_at = now
    task.postponed_until = None
    repo.save(task)
    if not task.recurrence or task.due_time is None:
        return None
    nxt = next_due(task.due_time, task.recurrence)
    if nxt is None:
        return None
    next_task = Task(
        id=new_id(),
        title=task.title,
        note=task.note,
        owner_id=task.owner_id,
        due_time=nxt,
        importance=task.importance,
        recurrence=task.recurrence,
        recurrence_group_id=task.recurrence_group_id or task.id,
        reminder_offset=task.reminder_offset,
    )
    repo.save(next_task)
    return next_task
