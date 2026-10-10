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


def test_new_task_dialog_opens_and_saves(qapp, window):
    """「+ 新任务」必须真的弹出一个能保存的表单。

    曾经 TaskDialog 连到一个不存在的方法，构造时就抛异常，点按钮毫无反应，
    而 GUI 进程没有控制台，错误被吞掉了。
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    shown = []

    def fill_and_close():
        dlg = next((w for w in qapp.topLevelWidgets()
                    if isinstance(w, QDialog) and w.isVisible()), None)
        assert dlg is not None, "点了「新任务」但没有模态窗出现"
        dlg.form.title_edit.setText("对话框新建")
        shown.append(dlg)
        dlg.form.save_btn.click()

    QTimer.singleShot(0, fill_and_close)
    window.open_new_task()

    assert len(shown) == 1
    assert not shown[0].isVisible(), "保存后对话框应当关闭"
    assert [t.title for t in _repo(window).list()] == ["对话框新建"]
    assert window.list.count() == 1, "保存后主列表要刷新"


def test_task_dialog_completes_and_rolls(qapp, window):
    """对话框里的「完成」走的是同一个关闭信号：关窗 + 周期任务滚到下一期。"""
    from yiriyishi.core.models import STATUS_TODO
    from yiriyishi.ui.task_form import TaskDialog

    repo = _repo(window)
    due = datetime.now() + timedelta(hours=1)
    task = repo.create(title="周期活", recurrence="daily", due_time=due)
    window.reload()

    dlg = TaskDialog(window.t, repo, window.person_repo, task, window)
    # 对话框本身没 show，子控件 isVisible() 恒为 False，只看「有没有被显式藏起来」
    assert not dlg.form.complete_btn.isHidden() and not dlg.form.delete_btn.isHidden()
    dlg.form.complete_btn.click()
    assert not dlg.isVisible()

    rolls = [t for t in repo.list() if t.status == STATUS_TODO]
    assert len(rolls) == 1 and rolls[0].id != task.id
    # 存进 sqlite 会抹掉微秒，比对要用同一精度
    assert rolls[0].due_time == due.replace(microsecond=0) + timedelta(days=1)


def test_new_task_stows_the_float_panel_first(qapp, window):
    """面板是置顶窗，模态框会被压在它下面：弹对话框前必须先收起面板。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    window.set_ball_visible(True, persist=False)
    window._toggle_panel(True)
    assert window._panel_open

    def check_and_close():
        dlg = next((w for w in qapp.topLevelWidgets()
                    if isinstance(w, QDialog) and w.isVisible()), None)
        assert dlg is not None, "点了「新任务」但没有模态窗出现"
        assert not window._panel_open, "对话框弹出来时面板还压在它上面"
        dlg.reject()

    QTimer.singleShot(0, check_and_close)
    window.open_new_task()



def test_task_form_fits_both_of_its_containers(qapp, window):
    """TaskForm 被两处复用：新任务对话框和右侧详情面板。

    它的布局最小宽度一度是 825px，比两个容器都宽，于是「明天 09:00」「本周五」
    「保存」被挤出可视区——在用户眼里就是按钮凭空消失。
    """
    from yiriyishi.ui.task_form import TaskDialog

    window.resize(1100, 720)
    window.show()
    qapp.processEvents()

    dlg = TaskDialog(window.t, window.task_repo, window.person_repo, None, window)
    need = dlg.form.minimumSizeHint()
    assert need.width() <= dlg.minimumWidth(), f"表单最小宽 {need.width()} > 对话框 {dlg.minimumWidth()}"
    assert need.width() <= window.detail.width(), f"表单最小宽 {need.width()} > 详情面板 {window.detail.width()}"
    dlg.show()
    qapp.processEvents()
    assert dlg.width() >= need.width() and dlg.height() >= need.height(), (
        f"对话框 {dlg.width()}x{dlg.height()} 装不下表单 {need.width()}x{need.height()}，底部按钮会被裁掉")


def test_settings_dialog_fits_chinese_and_persists(qapp, window, monkeypatch):
    """设置窗三件事：内容别被裁掉「确定」、按钮别是英文 OK/Cancel、改了要落盘。"""
    from PySide6.QtWidgets import QDialogButtonBox

    from yiriyishi.core.config import load_settings
    from yiriyishi.ui import settings_dialog

    # 真注册表碰不得：自启读写在测试里全部换成假实现
    calls = []
    monkeypatch.setattr(settings_dialog.autostart, "is_enabled", lambda *a: False)
    monkeypatch.setattr(settings_dialog.autostart, "enable",
                        lambda *a, **k: calls.append("on") or (True, ""))
    monkeypatch.setattr(settings_dialog.autostart, "disable",
                        lambda *a, **k: calls.append("off") or (True, ""))

    dlg = settings_dialog.SettingsDialog(window.t, window.paths, window.settings,
                                         window.apply_theme_mode, window)
    dlg.show()
    qapp.processEvents()

    need = dlg.layout().minimumSize()
    assert need.width() <= dlg.width() and need.height() <= dlg.height(), (
        f"设置内容 {need.width()}x{need.height()} 装不进 {dlg.width()}x{dlg.height()}")

    box = dlg.findChild(QDialogButtonBox)
    assert sorted(b.text() for b in box.buttons()) == sorted(["确定", "取消"])

    dlg.urgent.setValue(45)
    box.button(QDialogButtonBox.StandardButton.Ok).click()

    assert load_settings(window.paths).priority.urgent_minutes == 45
    assert calls == ["off"], "没勾自启时应该走 disable，而且只能是假实现"


def test_form_field_columns_line_up(qapp, window, monkeypatch):
    """表单对齐：标签列左边缘一致，字段列左边缘一致。

    曾经「标题」行挂在 QFormLayout 外面、_wrap 的子控件带着 Qt 默认边距，
    两处都会让字段左边缘参差，肉眼看着就是"没对齐"。
    """
    from PySide6.QtWidgets import QDialogButtonBox, QPushButton

    from yiriyishi.ui import settings_dialog
    from yiriyishi.ui.task_form import TaskDialog

    monkeypatch.setattr(settings_dialog.autostart, "is_enabled", lambda *a: False)
    monkeypatch.setattr(settings_dialog.autostart, "enable", lambda *a, **k: (True, ""))
    monkeypatch.setattr(settings_dialog.autostart, "disable", lambda *a, **k: (True, ""))

    task = TaskDialog(window.t, window.task_repo, window.person_repo, None, window)
    task.show()
    qapp.processEvents()
    quick = next(b for b in task.findChildren(QPushButton) if b.text() == "今天 18:00")
    task_fields = {
        "标题": task.form.title_edit, "负责人": task.form.owner,
        "完成时间": task.form.due_enable, "快捷时间": quick,
        "周期": task.form.recurrence, "重要度": task.form.importance,
        "提前提醒": task.form.reminder,
    }
    _assert_one_column(task, task_fields)
    task.deleteLater()

    settings = settings_dialog.SettingsDialog(window.t, window.paths, window.settings,
                                              window.apply_theme_mode, window)
    settings.show()
    qapp.processEvents()
    _assert_one_column(settings, {
        "紧急阈值": settings.urgent, "临近阈值": settings.warning,
        "跨天临近": settings.near, "本周内": settings.week,
        # 只量每行第一个控件：静默结束跟在同一行"至"后面，本就不在字段列上
        "静默开始": settings.dnd_start,
        "主题": settings.theme, "备份目录": settings.backup_dir,
    })
    settings.deleteLater()


def _assert_one_column(host, fields):
    edges = {name: w.mapTo(host, w.rect().topLeft()).x() for name, w in fields.items()}
    bad = {n: x for n, x in edges.items() if x != min(edges.values())}
    assert not bad, f"字段列左边缘不齐：{edges}"


def test_real_click_on_row_loads_detail_and_survives_refresh(qapp, window):
    """鼠标真实点击一行要能选中并联动右侧详情，且 30 秒刷新后不丢。

    以前同名链路只被 select_task()+_sync_detail() 手动调用覆盖过，
    绕开了 currentItemChanged 这条真实信号，点不动这类问题测不出来。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    task = _repo(window).create(title="点我", importance=3)
    window.reload()
    window.resize(1120, 720)
    window.show()
    qapp.processEvents()

    row = window.list.item(0)
    assert row.data(ROLE_TASK).id == task.id
    QTest.mouseClick(window.list.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     window.list.visualItemRect(row).center())
    qapp.processEvents()

    assert window.list.currentRow() == 0, "真实点击没选中行"
    assert window.detail.title_edit.text() == "点我"

    window.reload()
    qapp.processEvents()
    assert window.list.currentRow() == 0, "定时刷新把选中行弄丢了"
    assert window.detail.title_edit.text() == "点我"


def test_non_default_saved_sort_does_not_crash_construction(qapp, gui_paths, monkeypatch):
    """存过"按到期时间"时，构造主窗不能炸。

    FilterBar.set_sort 在信号已连接后才恢复排序，索引一变就同步触发
    _on_sort → reload()，而 self.list 还没建出来。打包成 GUI 后没有控制台，
    这条 AttributeError 只留在日志里，表现就是排序设置悄悄失效。
    """
    import sys

    from yiriyishi.core.config import load_settings, save_settings
    from yiriyishi.ui.main_window import MainWindow

    settings = load_settings(gui_paths)
    settings.default_sort = "due"
    save_settings(gui_paths, settings)

    caught = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: caught.append(value))

    window = MainWindow(gui_paths)
    try:
        assert not caught, f"构造主窗时槽函数抛了异常：{caught}"
        assert window.filter.sort.currentData() == "due"
        assert window.list is not None
    finally:
        window.quit_app()
