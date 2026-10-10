from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from yiriyishi.core import priority
from yiriyishi.core.models import PrioritySettings, STATUS_DONE, Task
from yiriyishi.core.priority import Bucket, Level

CFG = PrioritySettings(urgent_minutes=30, warning_hours=8, near_days=1, week_days=3)


def task(due=None, importance=3, status="todo", title="t", **kw) -> Task:
    return Task(title=title, due_time=due, importance=importance, status=status, **kw)


def lvl(due, now=datetime(2026, 10, 9, 12), importance=3, **kw) -> Level:
    return priority.compute_priority(task(due=due, importance=importance, **kw), now, CFG).level


# ---------------- 验收标准 1：颜色由完成日期自动推导 ----------------
def test_due_in_one_hour_is_warning_yellow(now):
    """1 小时后到期 → 黄（不是红）。这是需求里最容易写错的一条。"""
    result = priority.compute_priority(task(due=now + timedelta(hours=1)), now, CFG)
    assert result.level is Level.WARNING
    assert result.minutes_left == pytest.approx(60)


def test_due_in_thirty_minutes_is_urgent_red(now):
    assert lvl(now + timedelta(minutes=30), now=now) is Level.URGENT


def test_due_in_31_minutes_is_still_warning(now):
    assert lvl(now + timedelta(minutes=31), now=now) is Level.WARNING


def test_overdue_is_overdue_level(now):
    assert lvl(now - timedelta(minutes=1), now=now) is Level.OVERDUE


def test_due_later_today_is_green(now):
    assert lvl(now + timedelta(hours=9), now=now) is Level.TODAY  # 21:00 仍是当天


def test_cross_day_thresholds(now):
    assert lvl(now + timedelta(hours=20), now=now) is Level.NEAR      # <1 天 → 黄
    assert lvl(now + timedelta(days=2), now=now) is Level.WEEK        # ≤3 天 → 浅绿
    assert lvl(now + timedelta(days=10), now=now) is Level.FUTURE     # 更远 → 灰


def test_no_due_time_is_normal(now):
    assert priority.compute_priority(task(), now, CFG).minutes_left is None
    assert lvl(None, now=now) is Level.NORMAL


def test_done_task_is_excluded(now):
    result = priority.compute_priority(
        task(due=now - timedelta(days=1), status=STATUS_DONE), now, CFG
    )
    assert result.level is Level.DONE
    assert result.score == -1000


# ---------------- 分数单调性：越紧急分越高 ----------------
@pytest.mark.parametrize(
    "later,lvl_name",
    [
        (timedelta(days=-2), "overdue"),
        (timedelta(minutes=10), "urgent"),
        (timedelta(hours=4), "warning"),
        (timedelta(hours=20), "near"),
        (timedelta(days=5), "future"),
    ],
)
def test_score_decreases_as_urgency_decreases(now, later, lvl_name):
    scores = [
        priority.compute_priority(task(due=now + delta), now, CFG).score
        for delta in (timedelta(days=-2), timedelta(minutes=10), timedelta(hours=4),
                      timedelta(hours=20), timedelta(days=5))
    ]
    assert scores == sorted(scores, reverse=True)
    assert priority.compute_priority(task(due=now + later), now, CFG).level.value == lvl_name


def test_importance_breaks_ties_within_same_level(now):
    low = priority.compute_priority(task(due=now + timedelta(hours=4), importance=1), now, CFG)
    high = priority.compute_priority(task(due=now + timedelta(hours=4), importance=5), now, CFG)
    assert low.level is high.level
    assert high.score > low.score


# ---------------- 推迟：不改 due_time，但颜色后移 ----------------
def test_postponed_until_shifts_level_without_touching_due(now):
    t = task(due=now + timedelta(minutes=20))
    t.postponed_until = now + timedelta(hours=3)
    assert lvl(t.due_time, now=now) is Level.URGENT
    assert priority.compute_priority(t, now, CFG).level is Level.WARNING
    assert t.due_time == now + timedelta(minutes=20)


def test_sort_puts_overdue_first_and_done_last(now):
    tasks = [
        task(title="done", due=now + timedelta(hours=1), status=STATUS_DONE),
        task(title="far", due=now + timedelta(days=8)),
        task(title="over", due=now - timedelta(hours=2)),
        task(title="soon", due=now + timedelta(minutes=10)),
    ]
    ordered = priority.sort_tasks(tasks, now, "priority", CFG)
    assert [t.title for t in ordered] == ["over", "soon", "far", "done"]


def test_sort_by_due_ignores_importance(now):
    tasks = [task(title="hi", due=now + timedelta(days=3), importance=5),
             task(title="lo", due=now + timedelta(hours=1), importance=1)]
    assert [t.title for t in priority.sort_tasks(tasks, now, "due", CFG)] == ["lo", "hi"]


def test_bucket_of_covers_categories(now):
    assert priority.bucket_of(task(due=now - timedelta(hours=1)), now) is Bucket.OVERDUE
    assert priority.bucket_of(task(due=now + timedelta(hours=1)), now) is Bucket.TODAY
    assert priority.bucket_of(task(due=now + timedelta(days=4)), now) is Bucket.FUTURE
    assert priority.bucket_of(task(due=now + timedelta(days=4), recurrence="daily"), now) is Bucket.RECURRING
    assert priority.bucket_of(task(status=STATUS_DONE), now) is Bucket.DONE


def test_thresholds_are_configurable(now):
    """用户把紧急阈值改成 2 小时后，1 小时到期应变红。"""
    loose = PrioritySettings(urgent_minutes=120, warning_hours=8)
    assert lvl(now + timedelta(hours=1), now=now, importance=3) is Level.WARNING
    assert priority.compute_priority(task(due=now + timedelta(hours=1)), now, loose).level is Level.URGENT


@pytest.mark.parametrize(
    "minutes,text",
    [(None, "未排期"), (45, "45 分钟后"), (-90, "已逾期 1 小时 30 分"), (1500, "1 天 1 小时后")],
)
def test_humanize(minutes, text):
    assert priority.humanize_left(minutes) == text
