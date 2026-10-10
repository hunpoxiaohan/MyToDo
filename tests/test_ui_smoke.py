from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from yiriyishi.core.db import init_db
from yiriyishi.core.models import Task
from yiriyishi.core.priority import Bucket, Level
from yiriyishi.core.repository import PersonRepository, TaskRepository
from yiriyishi.ui.filter_bar import FilterBar
from yiriyishi.ui.float_panel import FloatBall, FloatPanel
from yiriyishi.ui.main_window import MainWindow
from yiriyishi.ui.task_form import OwnerCombo, TaskForm
from yiriyishi.ui.task_list import ROLE_RESULT, ROLE_TASK, TaskList
from yiriyishi.ui.theme import refresh_tokens, resolve_mode, tokens

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def gui_paths(paths, conn):
    """主窗口自己会开连接，这里只保证 schema 已建好。"""
    init_db(conn)
    conn.close()
    return paths


@pytest.fixture
def window(qapp, gui_paths):
    """和 main.build() 走同一条路：先换肤再建窗，否则测的是没样式的界面。"""
    from yiriyishi.ui.theme import apply_theme

    w = MainWindow(gui_paths)
    apply_theme(qapp, w.t)
    yield w
    w.quit_app()


def _repo(window) -> TaskRepository:
    return TaskRepository(window.conn)


def test_main_window_builds_and_lists_tasks(qapp, window):
    repo = _repo(window)
    repo.create(title="今天的活", due_time=datetime.now() + timedelta(minutes=10))
    repo.create(title="下周的活", due_time=datetime.now() + timedelta(days=8))
    window.reload()
    assert window.list.count() == 2
    assert "待办 2" in window.status_label.text()


def test_list_is_ordered_by_computed_priority(qapp, window):
    repo = _repo(window)
    repo.create(title="遥远", due_time=datetime.now() + timedelta(days=30))
    repo.create(title="马上", due_time=datetime.now() + timedelta(minutes=5))
    window.reload()
    titles = [window.list.item(i).data(ROLE_TASK).title for i in range(window.list.count())]
    assert titles == ["马上", "遥远"]


def test_low_priority_task_due_in_one_hour_is_warning_yellow(qapp, window):
    """验收标准 1 在真实控件上的体现：色条取到 WARNING 的黄。"""
    repo = _repo(window)
    repo.create(title="一小时内", due_time=datetime.now() + timedelta(minutes=60), importance=1)
    window.reload()
    result = window.list.item(0).data(ROLE_RESULT)
    assert result.level is Level.WARNING
    assert window.t.level(result.level.value) == "#F39C12"


def test_category_filter_narrows_the_list(qapp, window):
    repo = _repo(window)
    repo.create(title="今日A", due_time=datetime.now() + timedelta(hours=2))
    repo.create(title="未来B", due_time=datetime.now() + timedelta(days=9))
    window.reload()
    window._on_category(Bucket.TODAY)
    titles = [window.list.item(i).data(ROLE_TASK).title for i in range(window.list.count())]
    assert titles == ["今日A"]
    window._on_category(None)
    assert window.list.count() == 2


def test_chip_labels_carry_counts(qapp, window):
    repo = _repo(window)
    repo.create(title="逾期X", due_time=datetime.now() - timedelta(hours=3))
    window.reload()
    texts = [btn.text() for btn in window.filter._chips.values()]
    assert any(t.startswith("逾期 1") for t in texts)


def test_search_filters_by_title(qapp, window):
    repo = _repo(window)
    repo.create(title="整理相册")
    repo.create(title="整理桌面")
    window.reload()
    window._on_search("相册")
    assert window.list.count() == 1


def test_owner_filter_and_combo(qapp, window):
    persons = PersonRepository(window.conn)
    zhang = persons.create("张三")
    repo = _repo(window)
    repo.create(title="张的活", owner_id=zhang.id)
    repo.create(title="无主的活")
    window.reload()
    window._on_owner(zhang.id)
    assert window.list.count() == 1
    window._on_owner(None)
    assert window.list.count() == 2


def test_clicking_list_row_fills_the_detail_form(qapp, window):
    repo = _repo(window)
    task = repo.create(title="详情联动", importance=4)
    window.reload()
    window.list.select_task(task.id)
    window._sync_detail()
    assert window.detail.title_edit.text() == "详情联动"
    assert window.detail.importance.currentData() == 4


def test_form_commit_writes_and_emits_saved(qapp, window):
    persons = PersonRepository(window.conn)
    persons.create("王五")
    form = TaskForm(window.t, _repo(window), persons)
    form.title_edit.setText("表单新增")
    form.owner.setCurrentIndex(max(0, form.owner.findData(persons.find_by_name("王五").id)))
    saved: list[str] = []
    form.saved.connect(saved.append)
    assert form.commit() is True
    assert saved
    stored = _repo(window).list()[0]
    assert stored.title == "表单新增"
    assert stored.owner_id == persons.find_by_name("王五").id


def test_form_rejects_empty_title(qapp, window):
    form = TaskForm(window.t, _repo(window), PersonRepository(window.conn))
    form.title_edit.setText("   ")
    assert form.commit() is False
    assert _repo(window).list() == []


def test_typing_unknown_owner_creates_person(qapp, window):
    persons = PersonRepository(window.conn)
    form = TaskForm(window.t, _repo(window), persons)
    form.title_edit.setText("新建人员")
    form.owner.setEditable(True)
    form.owner.setEditText("赵六")          # 模拟输入新名字
    form.commit()
    assert persons.find_by_name("赵六") is not None
    assert _repo(window).list()[0].owner_id == persons.find_by_name("赵六").id


def test_selecting_recurring_rule_sets_group_id(qapp, window):
    repo = _repo(window)
    task = repo.create(title="每日站会", due_time=datetime.now() + timedelta(hours=1),
                       recurrence="daily")
    assert task.recurrence_group_id == task.id
    assert priority_bucket(window, task.id) is Bucket.RECURRING


def priority_bucket(window, task_id: str) -> Bucket:
    from yiriyishi.core.priority import bucket_of

    task = _repo(window).get(task_id)
    return bucket_of(task, datetime.now())


def test_theme_switch_updates_tokens_without_rebuilding(qapp, window):
    """验收标准 5：切深色后所有 token 同步换色，文字不会残留旧色。"""
    before = window.t.c("text")
    window.apply_theme_mode("dark")
    assert window.t.mode == "dark"
    assert window.t.c("text") != before
    assert window.list.delegate.t.c("text") == window.t.c("text")   # 同一 token 对象
    window.apply_theme_mode("light")
    assert window.t.c("text") == before


def test_level_colors_exist_in_both_modes(qapp, window):
    for mode in ("light", "dark"):
        refresh_tokens(window.t, mode)
        for level in Level:
            if level is Level.DONE:
                continue
            assert window.t.level(level.value).startswith("#"), f"{mode}/{level} 缺色"


def test_window_delivers_reminders_it_received(qapp, window):
    """扫描线程只产出 Reminder，发送与横幅由主窗口（GUI 线程）完成。"""
    from yiriyishi.core.scheduler import ReminderChecker

    class FakeNotifier:
        supports_activation = True

        def __init__(self):
            self.sent = []

        def notify(self, notification):
            self.sent.append(notification)
            return True

    fake = FakeNotifier()
    window.notifier = fake
    repo = _repo(window)
    task = repo.create(title="到点任务", due_time=datetime.now() + timedelta(minutes=5),
                       reminder_offset=15)

    fired = ReminderChecker(repo, lambda: window.settings).tick()
    window.deliver_reminders(fired)

    assert [n.task_id for n in fake.sent] == [task.id]
    assert window.toast.visible_task_id == task.id
    assert repo.get(task.id).last_notified_at is not None


def test_rows_ball_and_panel_actually_paint(qapp, window):
    """offscreen 下 grab() 会真的走 paintEvent/delegate.paint，能抓住枚举名一类的运行期错误。"""
    persons = PersonRepository(window.conn)
    owner = persons.create("周八")
    repo = _repo(window)
    late = repo.create(title="逾期任务", due_time=datetime.now() - timedelta(hours=2),
                       importance=5)
    late.owner_id = owner.id
    repo.save(late)
    repo.create(title="未来任务", due_time=datetime.now() + timedelta(days=4), importance=2)

    window.show()
    window.reload()
    window._tick_flash()
    window.toast.show_message("绘制冒烟", late.id)
    window.toast.setMaximumHeight(60)      # 跳过滑入动画，直接测绘制
    qapp.processEvents()

    for widget in (window, window.list, window.ball, window.toast, window.detail):
        image = widget.grab()
        assert not image.isNull()
        assert image.width() > 0 and image.height() > 0

    window._toggle_panel(True)
    qapp.processEvents()
    panel_image = window.panel.grab()
    assert not panel_image.isNull()
    window._toggle_panel(False)


def test_float_ball_badge_counts_overdue_plus_today(qapp, window):
    repo = _repo(window)
    repo.create(title="逾期", due_time=datetime.now() - timedelta(hours=1))
    repo.create(title="今日", due_time=datetime.now() + timedelta(hours=3))
    repo.create(title="未来", due_time=datetime.now() + timedelta(days=5))
    window.reload()
    assert window.ball._count == 2


def test_float_panel_groups_tasks_by_category(qapp, window):
    from yiriyishi.core import priority

    repo = _repo(window)
    task = repo.create(title="面板里的任务", due_time=datetime.now() + timedelta(minutes=40))
    persons = PersonRepository(window.conn)
    zhang = persons.create("孙七")
    task.owner_id = zhang.id
    repo.save(task)

    groups = priority_groups(window)
    assert Bucket.TODAY in groups
    row_task, result, owner = groups[Bucket.TODAY][0]
    assert row_task.title == "面板里的任务"
    assert owner == "孙七"
    assert result.level is Level.WARNING            # 40 分钟 > 30 分钟阈值

    window._toggle_panel(True)
    ids: list[str] = []
    window.panel.task_activated.connect(ids.append)
    window.panel.task_activated.emit(task.id)
    assert ids == [task.id]
    window._toggle_panel(False)


def priority_groups(window):
    from yiriyishi.ui.main_window import _groups

    names = {p.id: p.name for p in PersonRepository(window.conn).list(active_only=False)}
    return _groups(TaskRepository(window.conn).list(), names, datetime.now(),
                   window.settings.priority)


def test_toast_banner_postpone_shortcut(qapp, window):
    repo = _repo(window)
    task = repo.create(title="推迟我", due_time=datetime.now() - timedelta(minutes=1))
    window.reload()
    window.toast.show_message("快到时间了", task.id)
    assert window.toast.visible_task_id == task.id
    window.postpone_task(task.id)
    assert _repo(window).get(task.id).postponed_until is not None
    assert _repo(window).get(task.id).last_notified_at is None    # 允许再次提醒


def test_close_event_hides_to_tray_instead_of_quitting(qapp, window):
    from PySide6.QtGui import QCloseEvent

    window.show()
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is False    # 关窗被拦截 = 进程不退
    assert window.isHidden()
    assert qapp.quitOnLastWindowClosed() is False


def test_draft_is_saved_and_restored(qapp, window, gui_paths):
    repo = _repo(window)
    task = repo.create(title="原稿")
    window.reload()
    window.list.select_task(task.id)
    window._sync_detail()
    window.detail.title_edit.setText("未保存的改动")
    window._write_draft()
    assert gui_paths.draft.exists()

    second = MainWindow(gui_paths)
    assert second.detail.title_edit.text() == "未保存的改动"
    # 恢复后草稿保留：它镜像的是「尚未落库的编辑」，保存或退出才清
    assert gui_paths.draft.exists()
    second.detail.commit()
    assert not gui_paths.draft.exists()
    second.quit_app()


def test_geometry_persists_across_reopen(qapp, window, gui_paths):
    from yiriyishi.core.config import load_settings

    window.resize(900, 600)
    window.save_geometry()
    assert load_settings(gui_paths).window_geometry["w"] == 900


def _done_buttons(widget):
    from PySide6.QtWidgets import QPushButton

    return [b for b in widget.findChildren(QPushButton) if b.objectName() == "RowDone"]


def test_panel_row_has_inline_complete_button(qapp, window):
    repo = _repo(window)
    task = repo.create(title="就地完成", due_time=datetime.now() + timedelta(minutes=20))
    window.reload()
    window._toggle_panel(True)
    assert len(_done_buttons(window.panel)) == 1
    window._toggle_panel(False)
    assert repo.get(task.id).status == "todo"


def test_panel_complete_finishes_task_and_rolls_recurrence(qapp, window):
    """面板行内 ✓ 一步到位：不唤起主窗，周期任务自动滚到下一期。"""
    repo = _repo(window)
    once = repo.create(title="交周报", due_time=datetime.now() + timedelta(minutes=20))
    loop = repo.create(title="晨会", due_time=datetime.now() + timedelta(minutes=10),
                       recurrence="daily")
    window.reload()
    window._toggle_panel(True)
    assert len(_done_buttons(window.panel)) == 2

    window.complete_task(once.id)
    window.complete_task(loop.id)

    assert repo.get(once.id).status == "done"
    assert repo.get(loop.id).status == "done"
    rolled = [t for t in repo.list() if t.title == "晨会" and t.status == "todo"]
    assert len(rolled) == 1 and rolled[0].due_time > loop.due_time
    window._toggle_panel(False)


def test_task_list_tracks_hover(qapp, window):
    assert window.list.hasMouseTracking()


def test_qss_declarations_use_supported_properties(qapp):
    """Qt QSS 属性写错只会静默失效（甚至不报 box-shadow），这里按声明逐条兜住。"""
    import re

    from yiriyishi.ui.theme import build_qss

    allowed = {
        "background", "background-color", "color", "border", "border-radius",
        "border-top", "border-bottom", "border-left", "border-right",
        "border-style", "border-color", "border-width", "border-top-left-radius",
        "padding", "padding-left", "padding-right", "padding-top", "padding-bottom",
        "margin", "margin-top", "margin-left", "min-height", "max-height",
        "min-width", "max-width", "width", "height", "left", "right", "top", "bottom",
        "font-size", "font-weight", "font-family", "font", "outline", "image",
        "selection-background-color", "selection-color", "subcontrol-origin",
        "subcontrol-position", "text-align", "spacing",
    }
    for mode in ("light", "dark"):
        body = build_qss(tokens(mode))
        for block in re.findall(r"\{\s*(.*?)\s*\}", body, re.S):
            for decl in block.split(";"):
                prop = decl.split(":")[0].strip()
                if not prop or prop.startswith("/*"):
                    continue
                assert prop in allowed, f"{mode} 主题有 Qt 不认识的属性: {prop!r}"


def test_qss_selectors_all_match_a_widget_name(qapp):
    """样式表里的 objectName 必须在代码里真实存在，否则规则永远命不中。"""
    import re
    from pathlib import Path

    from yiriyishi.ui.theme import build_qss

    ui_dir = Path(__file__).resolve().parent.parent / "src" / "yiriyishi" / "ui"
    code = "\n".join(f.read_text(encoding="utf-8") for f in ui_dir.glob("*.py"))
    declared = set(re.findall(r'setObjectName\("([^"]+)"\)', code))
    from PySide6 import QtWidgets

    known_classes = {n for n in dir(QtWidgets) if n.startswith("Q")}
    # 只看选择器部分（每个 { 之前的文本），否则会误伤 #RRGGBB 颜色值
    selectors = re.findall(r"([^{}]+)\{", build_qss(tokens("light")))
    for part in selectors:
        for name in re.findall(r"#(\w+)", part):
            assert name in declared, f"QSS 里的 #{name} 没有任何控件在用"
        for sel in re.findall(r"\b(Q\w+)", part):
            assert sel in known_classes, f"QSS 选择了不存在的控件类型 {sel}"

def test_float_windows_only_transparent_outside_their_shape(qapp, window):
    """形状之外可以透明（边缘要靠 alpha 抗锯齿），但那部分必须被 mask 排除，
    否则就是「看不见的区域照样吞点击与滚轮」。"""
    window.set_ball_visible(True, persist=False)
    window._toggle_panel(True)
    for widget in (window.ball, window.panel):
        mask = widget.mask()
        assert not mask.isEmpty()
        assert mask.contains(widget.rect().center())
        assert not mask.contains(widget.rect().topLeft())
    window._toggle_panel(False)


def test_float_ball_and_panel_bodies_paint_opaque(qapp, window):
    """本体像素 alpha 必须是 255：半透明的球在桌面上看着像糊了。"""
    from PySide6.QtGui import QImage

    window._toggle_panel(True)
    for widget in (window.ball, window.panel):
        image = widget.grab().toImage().convertToFormat(QImage.Format_ARGB32)
        center = widget.rect().center()
        assert image.pixelColor(center).alpha() == 255
    window._toggle_panel(False)

def test_panel_rows_fit_the_visible_width(qapp, window):
    """行不能比面板宽：溢出会把「完成」推出可视区，还会冒出横向滚动条。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QPushButton

    repo = _repo(window)
    repo.create(title="一条标题特别特别长的任务用来测试溢出行为",
                due_time=datetime.now() + timedelta(minutes=20))
    window._toggle_panel(True)
    scroll = window.panel._scroll
    assert scroll.horizontalScrollBar().maximum() == 0
    viewport = scroll.viewport().width()
    for row in window.panel._rows:
        done = [b for b in row.findChildren(QPushButton) if b.objectName() == "RowDone"]
        assert done
        left = done[0].mapTo(scroll, QPoint(0, 0)).x()
        assert left + done[0].width() <= viewport    # 「完成」必须整个露在外面
    window._toggle_panel(False)

def test_float_ball_edge_is_antialiased_inside_the_mask(qapp, window):
    """圆边要有一圈渐隐像素，而且这些像素必须落在 mask 内：
    mask 切进渐变带 = 边缘重新变成锯齿。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage

    window.set_ball_visible(True, persist=False)
    image = window.ball.grab().toImage().convertToFormat(QImage.Format_ARGB32)
    y = window.ball.height() // 2
    ramp = [image.pixelColor(x, y).alpha() for x in range(image.width())]
    partial = [x for x, a in enumerate(ramp) if 0 < a < 255]
    assert partial, "球边没有半透明过渡像素，说明又被切成了锯齿"
    mask = window.ball.mask()
    assert all(mask.contains(QPoint(x, y)) for x in partial)
