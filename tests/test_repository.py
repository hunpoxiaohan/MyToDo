from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from yiriyishi.core.models import STATUS_ARCHIVED, STATUS_DONE, STATUS_TODO, Task
from yiriyishi.core.recurrence import complete_and_roll, next_due
from yiriyishi.core.repository import PersonRepository, TaskRepository


@pytest.fixture
def people(conn):
    return PersonRepository(conn)


@pytest.fixture
def repo(conn):
    return TaskRepository(conn)


def _t(due=None, **kw):
    return Task(title=kw.pop("title", "任务"), due_time=due, **kw)


# ---------------- 人员 ----------------
def test_person_create_and_dedupe_by_name(people):
    a = people.create("张三")
    b = people.create(" 张三 ")
    assert a.id == b.id
    assert len(people.list()) == 1


def test_deactivated_person_disappears_from_dropdown_but_history_keeps(people, repo):
    zhang = people.create("张三")
    task = repo.create(title="旧任务", owner_id=zhang.id)
    people.set_active(zhang.id, False)
    assert [p.name for p in people.list()] == []
    assert [p.name for p in people.list(active_only=False)] == ["张三"]
    assert repo.get(task.id).owner_id == zhang.id


def test_delete_person_unassigns_their_tasks(people, repo):
    li = people.create("李四")
    task = repo.create(title="交接前", owner_id=li.id)
    people.delete(li.id)
    assert repo.get(task.id).owner_id is None


# ---------------- 任务 CRUD ----------------
def test_save_and_roundtrip_all_fields(repo):
    due = datetime(2026, 10, 10, 9, 30)
    task = repo.create(title="写周报", due_time=due, importance=4, reminder_offset=60)
    loaded = repo.get(task.id)
    assert loaded.title == "写周报"
    assert loaded.due_time == due
    assert loaded.importance == 4
    assert loaded.reminder_offset == 60
    assert loaded.status == STATUS_TODO


def test_update_does_not_create_second_row(repo):
    task = repo.create(title="初稿")
    task.title = "终稿"
    repo.save(task)
    assert len(repo.list()) == 1
    assert repo.list()[0].title == "终稿"


def test_archived_hidden_unless_requested(repo):
    repo.create(title="在办")
    repo.create(title="归档", status=STATUS_ARCHIVED)
    assert {t.title for t in repo.list()} == {"在办"}
    assert "归档" in {t.title for t in repo.list(include_archived=True)}


def test_stats_counts_overdue_and_done_today(repo, now):
    repo.create(title="逾期", due_time=now - timedelta(hours=2))
    repo.create(title="今日", due_time=now + timedelta(hours=2))
    repo.create(title="明天", due_time=now + timedelta(days=1))
    done = repo.create(title="已完成", due_time=now)
    done.status = STATUS_DONE
    done.completed_at = now
    repo.save(done)
    stats = TaskRepository(repo._conn).stats(now)
    assert stats == {"open": 3, "overdue": 1, "due_today": 2, "done_today": 1}


# ---------------- 提醒去重 ----------------
def test_notification_candidates_respect_reminder_offset(repo, now):
    soon = repo.create(title="提前 15 分钟", due_time=now + timedelta(minutes=10), reminder_offset=15)
    repo.create(title="还早", due_time=now + timedelta(hours=5), reminder_offset=15)
    ids = [t.id for t in repo.notification_candidates(now)]
    assert ids == [soon.id]


def test_mark_notified_removes_from_candidates(repo, now):
    task = repo.create(title="一次性", due_time=now - timedelta(minutes=1))
    assert repo.notification_candidates(now)
    repo.mark_notified(task.id, now)
    assert repo.notification_candidates(now) == []


def test_postponing_clears_notified_so_it_fires_again(repo, now):
    task = repo.create(title="推迟测试", due_time=now - timedelta(minutes=1))
    repo.mark_notified(task.id, now)
    repo.postpone(task.id, now + timedelta(minutes=15))
    reloaded = repo.get(task.id)
    assert reloaded.last_notified_at is None
    assert reloaded.postponed_until == now + timedelta(minutes=15)
    assert reloaded.due_time == task.due_time  # 原始到期时间不动


# ---------------- 周期任务 ----------------
@pytest.mark.parametrize(
    "rule,expected",
    [
        ("daily", datetime(2026, 10, 10, 9)),
        ("every_n_days:3", datetime(2026, 10, 12, 9)),
        ("weekly:1,3,5", datetime(2026, 10, 12, 9)),   # 10-09 是周五 → 下一个周一
        ("monthly:15", datetime(2026, 11, 15, 9)),
    ],
)
def test_next_due_by_rule(rule, expected):
    assert next_due(datetime(2026, 10, 9, 9, 0), rule) == expected


def test_monthly_31_clamps_to_short_month():
    assert next_due(datetime(2026, 1, 31, 9), "monthly:31") == datetime(2026, 2, 28, 9)


def test_unknown_rule_returns_none():
    assert next_due(datetime(2026, 10, 9, 9), "yearly") is None


def test_complete_rolls_next_instance_sharing_group(repo):
    task = repo.create(title="喝水", due_time=datetime(2026, 10, 9, 9), recurrence="daily")
    rolled = complete_and_roll(repo, task, datetime(2026, 10, 9, 12))
    assert rolled is not None
    assert rolled.due_time == datetime(2026, 10, 10, 9)
    assert rolled.recurrence_group_id == task.recurrence_group_id == task.id
    done, nxt = repo.get(task.id), repo.get(rolled.id)
    assert done.status == STATUS_DONE and done.completed_at == datetime(2026, 10, 9, 12)
    assert nxt.status == STATUS_TODO


def test_completed_recurring_shows_only_current_period(repo):
    """列表里同一周期任务只出现当期那一条，历史已完成期数沉到「已完成」。"""
    first = repo.create(title="打卡", due_time=datetime(2026, 10, 9, 9), recurrence="daily")
    complete_and_roll(repo, first, datetime(2026, 10, 9, 12))
    visible = repo.list()
    todos = [t for t in visible if t.status == STATUS_TODO]
    assert len(todos) == 1
    assert todos[0].due_time == datetime(2026, 10, 10, 9)
    assert len(visible) == 2  # 历史期数仍在，只是不占用「周期」分类


def test_non_recurring_complete_creates_nothing(repo):
    task = repo.create(title="一次性", due_time=datetime(2026, 10, 9, 9))
    assert complete_and_roll(repo, task, datetime(2026, 10, 9, 12)) is None
    assert len(repo.list()) == 1
