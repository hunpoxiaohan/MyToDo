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


# ---------------- 任务笔记（带时间戳的过程记录） ----------------
@pytest.fixture
def notes(conn):
    from yiriyishi.core.repository import NoteRepository

    return NoteRepository(conn)


@pytest.fixture
def notepad(conn):
    """笔记必须挂在真实任务上（外键是开的），所以先建任务再返回 (笔记仓库, 任务甲 id, 任务乙 id)。"""
    from yiriyishi.core.repository import NoteRepository, TaskRepository

    tasks = TaskRepository(conn)
    a = tasks.create(title="任务甲")
    b = tasks.create(title="任务乙")
    return NoteRepository(conn), a.id, b.id


def test_note_add_and_list_is_oldest_first(notepad):
    """记录按写入先后返回，最新的追加在尾部；顺序由 created_at + 插入顺序决定。"""
    notes, t1, _ = notepad
    a = notes.add(t1, "开始查日志")
    b = notes.add(t1, "定位到 scheduler 线程")
    assert a.body == "开始查日志"
    assert a.created_at is not None
    assert [n.body for n in notes.list(t1)] == ["开始查日志", "定位到 scheduler 线程"]
    assert notes.get(b.id).body == "定位到 scheduler 线程"


def test_notes_are_scoped_to_one_task(notepad):
    notes, t1, t2 = notepad
    notes.add(t1, "属于甲")
    notes.add(t2, "属于乙")
    assert [n.body for n in notes.list(t1)] == ["属于甲"]
    assert notes.list("no-such-task") == []


def test_note_update_stamps_updated_at(notepad):
    notes, t1, _ = notepad
    created = notes.add(t1, "初稿")
    assert created.updated_at is None
    later = datetime(2026, 10, 9, 15, 30, 0)
    edited = notes.update(created.id, "改过了", when=later)
    assert edited.body == "改过了"
    assert edited.updated_at == later
    assert edited.created_at == created.created_at  # 添加时间不能被编辑冲掉


def test_note_delete(notepad):
    notes, t1, _ = notepad
    kept = notes.add(t1, "留着")
    gone = notes.add(t1, "删掉")
    notes.delete(gone.id)
    assert [n.id for n in notes.list(t1)] == [kept.id]


def test_deleting_task_removes_its_notes(repo, notes):
    """删任务时记录跟着走（外键 CASCADE），不留孤儿。"""
    task = repo.create(title="要被删的任务")
    notes.add(task.id, "过程记录")
    repo.delete(task.id)
    assert notes.list(task.id) == []
    assert repo._conn.execute("SELECT COUNT(*) FROM task_notes").fetchone()[0] == 0


def test_init_db_adds_note_table_to_existing_database(paths):
    """老库（version 1，只有 tasks/people）升级后要有 task_notes，且原数据不动。"""
    from yiriyishi.core.db import connect, init_db
    from yiriyishi.core.repository import NoteRepository

    old = connect(paths.db)
    old.executescript(
        """
        CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT,
                             sort_order INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, note TEXT,
                            owner_id TEXT, due_time TIMESTAMP, importance INTEGER DEFAULT 3,
                            status TEXT DEFAULT 'todo', recurrence TEXT,
                            recurrence_group_id TEXT, created_at TIMESTAMP, completed_at TIMESTAMP,
                            reminder_offset INTEGER DEFAULT 15, last_notified_at TIMESTAMP,
                            postponed_until TIMESTAMP);
        PRAGMA user_version=1;
        """
    )
    old.execute("INSERT INTO tasks (id, title) VALUES ('t1', '升级前就存在的任务')")
    old.commit()
    assert init_db(old) >= 2
    assert old.execute("PRAGMA user_version").fetchone()[0] >= 2
    NoteRepository(old).add("t1", "升级后新记的一条")
    assert TaskRepository(old).get("t1").title == "升级前就存在的任务"
    old.close()


# ---------------- 周期任务：锚点 / 截止日 / 每天重复提醒 ----------------
def test_init_db_adds_recurrence_columns_to_existing_database(paths):
    """老库升级要补三列。IF NOT EXISTS 只建表不给已存在的表加列，所以单独 ALTER。"""
    from yiriyishi.core.db import connect, init_db

    old = connect(paths.db)
    old.executescript(
        """
        CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT,
                             sort_order INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, note TEXT,
                            owner_id TEXT, due_time TIMESTAMP, importance INTEGER DEFAULT 3,
                            status TEXT DEFAULT 'todo', recurrence TEXT,
                            recurrence_group_id TEXT, created_at TIMESTAMP, completed_at TIMESTAMP,
                            reminder_offset INTEGER DEFAULT 15, last_notified_at TIMESTAMP,
                            postponed_until TIMESTAMP);
        PRAGMA user_version=2;
        """
    )
    old.execute("INSERT INTO tasks (id, title) VALUES ('t1', '升级前就存在的任务')")
    old.commit()
    init_db(old)
    cols = {r[1] for r in old.execute("PRAGMA table_info(tasks)")}
    assert {"recurrence_anchor", "recurrence_until", "repeat_reminder_daily"} <= cols
    row = old.execute("SELECT recurrence_anchor FROM tasks WHERE id='t1'").fetchone()
    assert row[0] == "schedule"          # 老任务落到「按日程」，与升级前的直觉一致
    old.close()


def test_recurrence_fields_round_trip(repo):
    task = repo.create(
        title="周报", due_time=datetime(2026, 10, 9, 9), recurrence="weekly:1,3,5",
        recurrence_anchor="completion", recurrence_until=datetime(2026, 11, 30, 23, 30),
        repeat_reminder_daily=True,
    )
    back = repo.get(task.id)
    assert back.recurrence_anchor == "completion"
    assert back.recurrence_until == datetime(2026, 11, 30, 23, 30)
    assert back.repeat_reminder_daily is True


def test_new_task_defaults_to_schedule_anchor(repo):
    task = repo.create(title="默认", due_time=datetime(2026, 10, 9, 9), recurrence="daily")
    assert task.recurrence_anchor == "schedule"
    assert task.recurrence_until is None
    assert task.repeat_reminder_daily is False


def test_schedule_anchor_does_not_drift_when_you_finish_late(repo):
    """「周一三五」不该因为某天没做就整条往后漂：晚做两天，下一期仍在原日程上。"""
    task = repo.create(title="站会", due_time=datetime(2026, 10, 9, 9),
                       recurrence="weekly:1,3,5", recurrence_anchor="schedule")
    rolled = complete_and_roll(repo, task, datetime(2026, 10, 11, 20, 0))   # 周五才补勾
    assert rolled.due_time == datetime(2026, 10, 12, 9)                     # 下一个周一，不是「周五+7」


def test_completion_anchor_counts_from_the_day_you_finished(repo):
    """「每 30 天换滤芯」这类要从实际完成日往后数，而不是死守日历格。"""
    task = repo.create(title="换滤芯", due_time=datetime(2026, 10, 9, 9),
                       recurrence="every_n_days:30", recurrence_anchor="completion")
    rolled = complete_and_roll(repo, task, datetime(2026, 10, 20, 15, 0))
    assert rolled.due_time == datetime(2026, 11, 19, 15)


def test_recurrence_stops_after_the_until_date_inclusive(repo):
    """截止日当天那一期仍然要生成，过了才停。"""
    task = repo.create(title="打卡", due_time=datetime(2026, 10, 9, 9), recurrence="daily",
                       recurrence_until=datetime(2026, 10, 10, 0, 0))
    rolled = complete_and_roll(repo, task, datetime(2026, 10, 9, 12))
    assert rolled.due_time == datetime(2026, 10, 10, 9)
    assert complete_and_roll(repo, repo.get(rolled.id), datetime(2026, 10, 10, 12)) is None


def test_daily_repeat_reminder_fires_again_the_next_day(repo, now):
    """没完成也每天到点再提醒一次 —— 这是「提醒」的重复，不是「任务」的重复。"""
    task = repo.create(title="喝水", due_time=now - timedelta(days=2, hours=3),
                       recurrence="daily", repeat_reminder_daily=True, reminder_offset=0)
    repo.mark_notified(task.id, now - timedelta(days=1, hours=5))   # 昨天已经提醒过
    assert [t.id for t in repo.notification_candidates(now)] == [task.id]


def test_daily_repeat_reminder_stays_quiet_after_firing_today(repo, now):
    task = repo.create(title="喝水", due_time=now - timedelta(days=2, hours=3),
                       recurrence="daily", repeat_reminder_daily=True, reminder_offset=0)
    repo.mark_notified(task.id, now - timedelta(hours=1))           # 今天已经提醒过
    assert repo.notification_candidates(now) == []


def test_daily_repeat_reminder_only_pings_on_scheduled_days(repo, now):
    """2026-10-09 是周五：周一/周三的档期，周五不该追着你提醒。"""
    task = repo.create(title="站会", due_time=now - timedelta(days=1, hours=3),
                       recurrence="weekly:1,3", repeat_reminder_daily=True, reminder_offset=0)
    repo.mark_notified(task.id, now - timedelta(days=1))
    assert repo.notification_candidates(now) == []


def test_daily_repeat_reminder_honours_the_snooze_you_just_set(repo, now):
    """刚点「15 分钟后再提醒」，下一轮扫描不该立刻又追上来。"""
    task = repo.create(title="喝水", due_time=now - timedelta(days=2, hours=3),
                       recurrence="daily", repeat_reminder_daily=True, reminder_offset=0)
    repo.mark_notified(task.id, now - timedelta(days=1))
    repo.postpone(task.id, now + timedelta(minutes=15))
    assert repo.notification_candidates(now) == []


def test_plain_recurring_task_still_notifies_only_once(repo, now):
    """没开重复提醒的周期任务保持原样：提醒过一次就闭嘴，直到下一期。"""
    task = repo.create(title="周报", due_time=now - timedelta(hours=3),
                       recurrence="weekly:1,3,5", reminder_offset=0)
    repo.mark_notified(task.id, now - timedelta(hours=2))
    assert repo.notification_candidates(now) == []


# ---------------- meta：调度线程要记住的零碎状态 ----------------
def test_meta_repository_round_trips_and_overwrites(conn):
    from yiriyishi.core.repository import MetaRepository

    meta = MetaRepository(conn)
    assert meta.get("last_morning_review") is None
    meta.set("last_morning_review", "2026-10-09")
    assert meta.get("last_morning_review") == "2026-10-09"
    meta.set("last_morning_review", "2026-10-10")
    assert meta.get("last_morning_review") == "2026-10-10"


# ---------------- 子任务：只有一层、各自带时间、随父任务级联 ----------------
def test_parent_id_migrates_into_an_existing_database(paths):
    """v4 老库要能补上 parent_id，补完还得真能挂父子。"""
    from yiriyishi.core.db import connect, init_db

    old = connect(paths.db)
    old.executescript(
        """
        CREATE TABLE people (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT,
                             sort_order INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
        CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, note TEXT,
                            owner_id TEXT, due_time TIMESTAMP, importance INTEGER DEFAULT 3,
                            status TEXT DEFAULT 'todo', recurrence TEXT,
                            recurrence_group_id TEXT, recurrence_anchor TEXT DEFAULT 'schedule',
                            recurrence_until TIMESTAMP, repeat_reminder_daily INTEGER DEFAULT 0,
                            created_at TIMESTAMP, completed_at TIMESTAMP,
                            reminder_offset INTEGER DEFAULT 15, last_notified_at TIMESTAMP,
                            postponed_until TIMESTAMP);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        PRAGMA user_version=4;
        """
    )
    old.execute("INSERT INTO tasks (id, title) VALUES ('p1', '搬家')")
    old.commit()
    init_db(old)
    cols = {r[1] for r in old.execute("PRAGMA table_info(tasks)")}
    assert "parent_id" in cols
    old.execute("INSERT INTO tasks (id, title, parent_id) VALUES ('c1', '打包书房', 'p1')")
    assert old.execute("SELECT parent_id FROM tasks WHERE id='c1'").fetchone()[0] == "p1"
    old.close()


def test_subtask_fields_round_trip(repo):
    parent = repo.create(title="搬家")
    child = repo.create(title="打包书房", parent_id=parent.id,
                        due_time=datetime(2026, 10, 13, 9))
    back = repo.get(child.id)
    assert back.parent_id == parent.id
    assert back.due_time == datetime(2026, 10, 13, 9)


def test_list_only_returns_top_level_tasks(repo):
    """主列表是「今天做哪一件」，子任务挤进来会把它拆碎。"""
    parent = repo.create(title="搬家")
    repo.create(title="打包书房", parent_id=parent.id)
    repo.create(title="退租", parent_id=parent.id)
    repo.create(title="买牛奶")
    assert sorted(t.title for t in repo.list()) == ["买牛奶", "搬家"]


def test_subtasks_come_back_ordered_by_due_then_created(repo):
    parent = repo.create(title="搬家")
    repo.create(title="换锁", parent_id=parent.id, due_time=datetime(2026, 10, 20, 9))
    repo.create(title="打包书房", parent_id=parent.id, due_time=datetime(2026, 10, 12, 9))
    repo.create(title="找纸箱", parent_id=parent.id)
    assert [t.title for t in repo.subtasks(parent.id)] == ["打包书房", "换锁", "找纸箱"]


def test_deleting_the_parent_takes_its_subtasks(repo):
    parent = repo.create(title="搬家")
    child = repo.create(title="打包书房", parent_id=parent.id)
    repo.delete(parent.id)
    assert repo.get(child.id) is None


def test_subtask_counts_roll_up_for_the_progress_badge(repo):
    parent = repo.create(title="搬家")
    repo.create(title="打包书房", parent_id=parent.id, status="done")
    repo.create(title="退租", parent_id=parent.id)
    repo.create(title="买牛奶")
    assert repo.subtask_counts() == {parent.id: (1, 2)}


def test_list_carries_rollup_for_priority_and_badge(repo):
    """父任务的颜色要跟着最早那个未完成子任务走，否则它会隐身到过期。"""
    parent = repo.create(title="搬家", due_time=datetime(2026, 10, 20, 9))
    repo.create(title="打包书房", parent_id=parent.id, due_time=datetime(2026, 10, 12, 9))
    shown = next(t for t in repo.list() if t.id == parent.id)
    assert shown.effective_due() == datetime(2026, 10, 12, 9)
    assert shown.scheduled_due() == datetime(2026, 10, 20, 9)
    assert (shown.subtask_done, shown.subtask_total) == (0, 1)


def test_done_subtasks_do_not_move_the_parent_date(repo):
    parent = repo.create(title="搬家", due_time=datetime(2026, 10, 20, 9))
    repo.create(title="打包书房", parent_id=parent.id,
                due_time=datetime(2026, 10, 11, 9), status="done")
    assert repo.get(parent.id).effective_due() == datetime(2026, 10, 20, 9)


def test_reminders_use_the_parents_own_date(repo, now):
    """父任务不该因为某个子任务已经到期就提前开炮 —— 子任务自己会提醒。"""
    parent = repo.create(title="搬家", due_time=now + timedelta(days=9))
    child = repo.create(title="打包书房", parent_id=parent.id,
                        due_time=now - timedelta(hours=2))
    assert [t.id for t in repo.notification_candidates(now)] == [child.id]


def test_rolling_a_recurring_parent_copies_its_subtasks(repo):
    """每周一的「周会」挂着议程子任务：滚到下周时子任务要跟着搬，
    勾选状态清零，日期按父任务的偏移平移。"""
    parent = repo.create(title="周会", due_time=datetime(2026, 10, 12, 9), recurrence="weekly:1")
    repo.create(title="准备议程", parent_id=parent.id, due_time=datetime(2026, 10, 12, 10))
    repo.create(title="发邀请", parent_id=parent.id, status=STATUS_DONE,
                due_time=datetime(2026, 10, 11, 15))
    rolled = complete_and_roll(repo, parent, datetime(2026, 10, 12, 12))

    kids = repo.subtasks(rolled.id)
    assert [k.title for k in kids] == ["发邀请", "准备议程"]      # 按到期日排
    assert all(k.status == STATUS_TODO for k in kids), "新一期从空白勾选开始"
    assert [k.due_time for k in kids] == [datetime(2026, 10, 18, 15), datetime(2026, 10, 19, 10)]
    assert all(not k.recurrence for k in kids), "子任务不继承周期，否则一期套一期"


def test_rolling_leaves_the_finished_period_subtasks_alone(repo):
    """上一期的子任务跟着上一期的父任务留在「已完成」，不该被平移走。"""
    parent = repo.create(title="周会", due_time=datetime(2026, 10, 9, 9), recurrence="daily")
    child = repo.create(title="准备议程", parent_id=parent.id, due_time=datetime(2026, 10, 9, 10))
    rolled = complete_and_roll(repo, parent, datetime(2026, 10, 9, 12))

    assert repo.get(child.id).parent_id == parent.id
    assert repo.get(child.id).due_time == datetime(2026, 10, 9, 10)
    assert len(repo.subtasks(rolled.id)) == 1


def test_subtasks_leave_archived_kids_out(repo):
    """树上画出来的子行必须和父行的 1/2 对得上，归档的那条两边都不算。"""
    parent = repo.create(title="搬新家")
    repo.create(title="打包书房", parent_id=parent.id)
    repo.create(title="旧物处理", parent_id=parent.id, status=STATUS_ARCHIVED)

    assert [t.title for t in repo.subtasks(parent.id)] == ["打包书房"]
    assert repo.subtask_counts()[parent.id] == (0, 1)


# ---------------- 状态：误点完成之后要能改回来 ----------------
def test_reopen_turns_a_done_task_back_into_todo(repo, now):
    task = repo.create(title="交房租")
    complete_and_roll(repo, task, now)
    repo.reopen(task.id)
    back = repo.get(task.id)
    assert back.status == STATUS_TODO
    assert back.completed_at is None


def test_reopen_on_an_open_task_is_a_no_op(repo):
    task = repo.create(title="还没做")
    repo.reopen(task.id)
    assert repo.get(task.id).status == STATUS_TODO


def test_reopen_takes_back_the_period_that_completion_rolled(repo, now):
    """滚出来的下一期是那次误点的产物：拉回当期时得一并收掉，否则库里留一条幽灵。"""
    task = repo.create(title="交房租", due_time=now, recurrence="daily")
    rolled = complete_and_roll(repo, task, now)
    assert rolled is not None
    repo.reopen(task.id)
    assert repo.get(rolled.id) is None
    assert repo.get(task.id).status == STATUS_TODO


def test_reopen_keeps_the_rolled_subtasks_deleted_too(repo, now):
    task = repo.create(title="搬新家", due_time=now, recurrence="daily")
    repo.create(title="打包书房", parent_id=task.id)
    rolled = complete_and_roll(repo, task, now)
    repo.reopen(task.id)
    assert repo.subtasks(rolled.id) == []
