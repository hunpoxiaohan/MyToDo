from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from .models import STATUS_DONE, Task

if TYPE_CHECKING:
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


def scheduled_on(due: datetime, rule: str, day: date) -> bool:
    """规则在指定这一天是否真的有一期 —— 「周一三五」的站会不该在周六追着你提醒。"""
    kind, _, arg = rule.partition(":")
    if kind == "daily":
        return True
    if kind == "every_n_days":
        n = int(arg) or 1
        return max(0, (day - due.date()).days) % n == 0
    if kind == "weekly":
        days = {int(d) for d in arg.split(",") if d.strip()}
        return day.isoweekday() in days
    if kind == "monthly":
        target = int(arg)
        return day.day == min(target, calendar.monthrange(day.year, day.month)[1])
    return False


def _next_after(due: datetime, rule: str, not_before: datetime, limit: int = 400) -> datetime | None:
    """按日程往后走，跳过已经错过的期数：晚做几天只补当期之后最近的一期，不堆积历史。"""
    current = due
    for _ in range(limit):
        current = next_due(current, rule)
        if current is None:
            return None
        if current > not_before:
            return current
    return None


def complete_and_roll(repo: TaskRepository, task: Task, now: datetime) -> Task | None:
    """完成当期；周期任务自动生成下一期。返回新建的下一期任务，或 None。"""
    task.status = STATUS_DONE
    task.completed_at = now
    task.postponed_until = None
    repo.save(task)
    if not task.recurrence or task.due_time is None:
        return None
    if task.recurrence_anchor == "completion":
        nxt = next_due(now, task.recurrence)
    else:
        nxt = _next_after(task.due_time, task.recurrence, now)
    if nxt is None:
        return None
    if task.recurrence_until and nxt.date() > task.recurrence_until.date():
        return None
    next_task = task.copy_for_next_period(nxt)
    repo.save(next_task)
    _roll_subtasks(repo, task, next_task, nxt - task.due_time)
    return next_task


def _roll_subtasks(repo: TaskRepository, old_parent: Task, next_parent: Task, offset: timedelta) -> None:
    """新一期从空白勾选开始：子任务只搬标题和时间，完成状态与推迟都不带过来。"""
    for child in repo.subtasks(old_parent.id):
        repo.save(Task(
            title=child.title,
            note=child.note,
            owner_id=child.owner_id,
            parent_id=next_parent.id,
            due_time=child.due_time + offset if child.due_time else None,
            importance=child.importance,
            reminder_offset=child.reminder_offset,
        ))
