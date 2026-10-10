from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from yiriyishi.core.db import connect, init_db
from yiriyishi.core.models import AppSettings
from yiriyishi.core.repository import TaskRepository
from yiriyishi.core.scheduler import ReminderChecker

NOW = datetime(2026, 10, 9, 12, 0, 0)


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "s.db")
    init_db(conn)
    repo = TaskRepository(conn)
    settings = AppSettings()
    return repo, settings, ReminderChecker(repo, lambda: settings)


def _due(minutes):
    return NOW + timedelta(minutes=minutes)


def test_fires_once_at_reminder_point(env):
    repo, _s, checker = env
    task = repo.create(title="交材料", due_time=_due(10), reminder_offset=15)
    fired = checker.tick(NOW)
    assert [r.task.id for r in fired] == [task.id]
    assert fired[0].notification.title.endswith("交材料")

    # 下一轮扫描不得重复轰炸
    assert checker.tick(NOW + timedelta(seconds=30)) == []


def test_not_fire_before_offset(env):
    repo, _s, checker = env
    repo.create(title="两小时后", due_time=_due(120), reminder_offset=15)
    assert checker.tick(NOW) == []


def test_paused_reminders_sends_nothing(env):
    repo, settings, checker = env
    repo.create(title="静音", due_time=_due(5))
    settings.reminders_paused = True
    assert checker.tick(NOW) == []


def test_quiet_hours_records_but_does_not_popup(env):
    """免打扰期内：不弹窗，但写 last_notified_at，避免时段结束后一次性轰炸。"""
    repo, settings, checker = env
    settings.dnd_enabled = True
    settings.dnd_start, settings.dnd_end = "22:00", "08:00"
    late = NOW.replace(hour=23)
    task = repo.create(title="深夜任务", due_time=late + timedelta(minutes=5), reminder_offset=15)

    assert checker.tick(late) == []
    assert checker.skipped_quiet == [task.id]
    assert repo.get(task.id).last_notified_at == late
    assert repo.notification_candidates(late + timedelta(minutes=1)) == []


def test_overdue_task_fires_immediately(env):
    repo, _s, checker = env
    repo.create(title="已逾期", due_time=_due(-60))
    assert len(checker.tick(NOW)) == 1


def test_done_task_is_not_reminded(env):
    repo, _s, checker = env
    repo.create(title="已完成", due_time=_due(5), status="done")
    assert checker.tick(NOW) == []


def test_postponed_task_waits_until_new_time(env):
    repo, _s, checker = env
    task = repo.create(title="推迟中的任务", due_time=_due(5), reminder_offset=15)
    repo.postpone(task.id, NOW + timedelta(minutes=40))
    assert checker.tick(NOW) == []          # 被推迟到 40 分钟后，现在不该响
    fired = checker.tick(NOW + timedelta(minutes=30))
    assert [r.task.id for r in fired] == [task.id]


def test_tick_marks_notified_even_if_delivery_fails(env):
    """扫描即标记，GUI 线程发送失败也不会每 30 秒无限重试。"""
    repo, _s, checker = env
    task = repo.create(title="发不出去", due_time=_due(5))
    fired = checker.tick(NOW)
    assert [r.task.id for r in fired] == [task.id]        # 仍回传给 UI 做应用内横幅
    assert repo.get(task.id).last_notified_at == NOW


def test_message_contains_remaining_time(env):
    repo, _s, checker = env
    repo.create(title="周报", due_time=_due(10), reminder_offset=15, importance=4)
    fired = checker.tick(NOW)
    assert fired
    body = fired[0].notification.message
    assert "10 分钟" in body and "4" in body


def test_reminder_carries_task_id_for_activation(env):
    """点击通知要能定位任务，所以 task_id 必须随通知带过去。"""
    repo, _s, checker = env
    task = repo.create(title="带 id 的任务", due_time=_due(3))
    fired = checker.tick(NOW)
    assert fired[0].notification.task_id == task.id
