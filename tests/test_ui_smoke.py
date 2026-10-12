from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QPushButton

from yiriyishi.core.db import init_db
from yiriyishi.core.models import Task
from yiriyishi.core.priority import Bucket, Level
from yiriyishi.core.repository import MetaRepository, PersonRepository, TaskRepository
from yiriyishi.ui.filter_bar import FilterBar
from yiriyishi.ui.float_panel import FloatBall, FloatPanel
from yiriyishi.ui.main_window import MainWindow
from yiriyishi.ui.note_timeline import ROLE_DAY, ROLE_NOTE
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

    fired = ReminderChecker(repo, lambda: window.settings, MetaRepository(window.conn)).tick()
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
# ---------------- 任务笔记：带时间戳的过程记录 ----------------
def _note_repo(window):
    from yiriyishi.core.repository import NoteRepository

    return NoteRepository(window.conn)

def _today_at(hour, minute=0):
    return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


def _note_item(tl, body):
    for i in range(tl.list.count()):
        note = tl.list.item(i).data(ROLE_NOTE)
        if note is not None and note.body == body:
            return tl.list.item(i)
    raise AssertionError("时间线里没有这条记录：" + body)


def _timeline_lines(tl):
    lines = []
    for i in range(tl.list.count()):
        item = tl.list.item(i)
        if item.data(ROLE_DAY):
            lines.append(("day", item.text()))
        else:
            lines.append(("note", item.data(ROLE_NOTE).body))
    return lines


def _buttons(row, name):
    from PySide6.QtWidgets import QPushButton

    return [b for b in row.findChildren(QPushButton) if b.objectName() == name]


def test_note_tab_only_exists_in_the_detail_panel(qapp, window):
    """新任务对话框里任务还不存在，记录 Tab 必须整个不出现，而不是显示一个空壳。"""
    from PySide6.QtWidgets import QTabWidget

    from yiriyishi.ui.task_form import TaskDialog

    assert isinstance(window.detail.notes_tab, QTabWidget)
    assert window.detail.notes_tab.tabText(0) == "备注"
    assert window.detail.notes_tab.tabText(1).startswith("记录")

    dlg = TaskDialog(window.t, window.task_repo, window.person_repo, None, window)
    assert dlg.form.findChild(QTabWidget) is None, "新任务对话框不该带记录 Tab"
    dlg.deleteLater()


def test_tab_title_carries_record_count(qapp, window):
    task = _repo(window).create(title="有记录的任务")
    notes = _note_repo(window)
    notes.add(task.id, "第一条")
    notes.add(task.id, "第二条")
    window.focus_task(task.id)
    qapp.processEvents()
    assert window.detail.notes_tab.tabText(1) == "记录(2)"


def test_timeline_groups_entries_by_day_newest_day_first(qapp, window):
    """按天分组、组头写日期、组内最新在上；条目只显示时分，完整时间进 tooltip。"""
    task = _repo(window).create(title="分组")
    notes = _note_repo(window)
    notes.add(task.id, "今天下午", when=_today_at(14))
    notes.add(task.id, "今天上午", when=_today_at(9))
    notes.add(task.id, "昨天", when=_today_at(10) - timedelta(days=1))
    notes.add(task.id, "四天前", when=_today_at(10) - timedelta(days=4))
    window.focus_task(task.id)
    qapp.processEvents()

    tl = window.detail.notes
    lines = _timeline_lines(tl)
    assert [kind for kind, _ in lines] == ["day", "note", "note", "day", "note", "day", "note"]
    assert lines[0][1] == "今天"
    assert [t for k, t in lines if k == "note"] == ["今天下午", "今天上午", "昨天", "四天前"]
    assert lines[3][1] == "昨天"
    assert lines[5][1].startswith((_today_at(10) - timedelta(days=4)).strftime("%Y-%m-%d"))

    row = tl.list.itemWidget(_note_item(tl, "今天下午"))
    assert row.time_label.text() == "14:00"
    assert _today_at(14).strftime("%Y-%m-%d") in row.time_label.toolTip()


def test_ctrl_enter_records_a_note_right_now(qapp, window):
    """输入 + Ctrl+Enter 立即入库并出现在今天那组的第一行，时间就是现在。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    task = _repo(window).create(title="正在做的事")
    window.focus_task(task.id)
    qapp.processEvents()

    tl = window.detail.notes
    tl.input.setPlainText("刚发现接口换了字段")
    QTest.keyClick(tl.input, Qt.Key_Return, Qt.ControlModifier)
    qapp.processEvents()

    stored = _note_repo(window).list(task.id)
    assert [n.body for n in stored] == ["刚发现接口换了字段"]
    # 第 0 行是「今天」组头，新记录排在组内第一行
    assert tl.list.item(0).data(ROLE_DAY) is not None
    assert tl.list.item(0).text() == "今天"
    assert tl.list.item(1).data(ROLE_NOTE).body == "刚发现接口换了字段"
    assert tl.input.toPlainText() == ""
    assert window.detail.notes_tab.tabText(1) == "记录(1)"


def test_blank_input_records_nothing(qapp, window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    task = _repo(window).create(title="空白测试")
    window.focus_task(task.id)
    qapp.processEvents()
    tl = window.detail.notes
    tl.input.setPlainText("   \n  ")
    QTest.keyClick(tl.input, Qt.Key_Return, Qt.ControlModifier)
    tl.add_btn.click()
    qapp.processEvents()
    assert _note_repo(window).list(task.id) == []
    assert tl.list.count() == 0


def test_actions_appear_on_the_current_row_and_delete_works(qapp, window):
    """悬停/选中才露出编辑与删除，点删除真的从库里消失。"""
    task = _repo(window).create(title="要删一条")
    notes = _note_repo(window)
    notes.add(task.id, "留着")
    notes.add(task.id, "删掉这条")
    window.focus_task(task.id)
    qapp.processEvents()

    tl = window.detail.notes
    idle = tl.list.itemWidget(_note_item(tl, "留着"))
    assert all(b.isHidden() for b in _buttons(idle, "NoteDelete"))

    tl.list.setCurrentItem(_note_item(tl, "删掉这条"))
    qapp.processEvents()
    row = tl.list.itemWidget(_note_item(tl, "删掉这条"))
    del_btn = _buttons(row, "NoteDelete")[0]
    assert not del_btn.isHidden(), "选中行要能把操作按钮露出来"
    del_btn.click()
    qapp.processEvents()

    assert [n.body for n in _note_repo(window).list(task.id)] == ["留着"]
    assert tl.list.count() == 2  # 组头 + 剩下那条


def test_double_click_edits_in_place_and_keeps_the_original_time(qapp, window):
    """双击原地改内容，添加时间不能被编辑冲掉。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    task = _repo(window).create(title="改一条")
    created = _note_repo(window).add(task.id, "写错了", when=_today_at(9))
    window.focus_task(task.id)
    qapp.processEvents()

    tl = window.detail.notes
    window.detail.notes_tab.setCurrentIndex(1)   # 记录页要真的看得见，鼠标事件才落得到行上
    window.resize(1120, 720)
    window.show()
    qapp.processEvents()

    item = _note_item(tl, "写错了")
    pos = tl.list.visualItemRect(item).center()
    # 真人双击必然带一次按下：QTest.mouseDClick 单发时 QAbstractItemView 的 pressedIndex 没设，信号不会出
    QTest.mouseClick(tl.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    QTest.mouseDClick(tl.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    qapp.processEvents()

    row = tl.list.itemWidget(item)
    assert row.editor.isVisible(), "双击后要能原地编辑"
    row.editor.setPlainText("改对了")
    QTest.keyClick(row.editor, Qt.Key_Return, Qt.ControlModifier)
    qapp.processEvents()

    after = _note_repo(window).get(created.id)
    assert after.body == "改对了"
    assert after.created_at == created.created_at
    assert after.updated_at is not None
    edited_row = tl.list.itemWidget(_note_item(tl, "改对了"))
    assert edited_row.time_label.text() == "09:00"


def test_timeline_is_empty_and_disabled_without_a_task(qapp, window):
    """没有选中任务时不能记东西，也不能留着上一个任务的记录。"""
    task = _repo(window).create(title="临时")
    _note_repo(window).add(task.id, "某条记录")
    window.focus_task(task.id)
    qapp.processEvents()
    assert window.detail.notes.list.count() == 2

    window.detail.clear_form()
    qapp.processEvents()
    assert window.detail.notes.list.count() == 0
    assert not window.detail.notes.input.isEnabled()
    assert not window.detail.notes.add_btn.isEnabled()


def test_switching_tasks_swaps_the_records(qapp, window):
    a = _repo(window).create(title="任务甲")
    b = _repo(window).create(title="任务乙")
    notes = _note_repo(window)
    notes.add(a.id, "甲的记录")
    notes.add(b.id, "乙的记录")
    notes.add(b.id, "乙的另一条")

    window.focus_task(a.id)
    qapp.processEvents()
    assert [t for k, t in _timeline_lines(window.detail.notes) if k == "note"] == ["甲的记录"]

    window.focus_task(b.id)
    qapp.processEvents()
    bodies = [t for k, t in _timeline_lines(window.detail.notes) if k == "note"]
    assert bodies == ["乙的另一条", "乙的记录"]
    assert window.detail.notes_tab.tabText(1) == "记录(2)"


# ---------------- 窗口缩放：详情面板滚动 / 分栏与几何恢复 ----------------

def _available_rect():
    from PySide6.QtWidgets import QApplication

    return QApplication.primaryScreen().availableGeometry()


def test_window_can_shrink_to_a_laptop_height(qapp, window):
    """详情表单的自然最小高 525 会把窗口顶到 712，768 高的笔记本屏放不下。

    表单套上滚动容器后窗口最小高要降到 520 以内，代码里的 resize(1060, 680) 才算真生效。
    """
    window.show()
    qapp.processEvents()
    # 没 show 过的窗口不会应用布局最小高，那样测的是个寂寞
    assert window.minimumHeight() <= 520, f"窗口最小高 {window.minimumHeight()}"
    window.resize(1060, 680)
    qapp.processEvents()
    assert window.height() == 680


def test_detail_pane_scrolls_instead_of_growing_the_window(qapp, window):
    """窗口压矮时，溢出的表单高度由详情面板自己滚动消化，而不是继续顶高窗口。"""
    window.resize(1060, 460)
    qapp.processEvents()
    area = window.detail_scroll
    assert area.viewport().height() < area.widget().sizeHint().height()
    assert area.verticalScrollBar().maximum() > 0, "表单溢出却没有可滚的范围"


def test_splitter_position_survives_reopen(qapp, window, gui_paths):
    """左右分栏拖到哪儿，重启后还在哪儿；setStretchFactor 从来没能生效过。"""
    window.show()
    # 760 而不是 1100：测试跑在 offscreen 的 800x800 虚拟屏上，超屏会被钳，测不到分栏本身
    window.resize(760, 620)
    qapp.processEvents()
    window.splitter.setSizes([460, 300])
    qapp.processEvents()
    before = window.splitter.sizes()
    # Qt 会按比例缩放到分栏的真实宽度，所以只能拿落定后的值做往返比较，不能断言绝对像素
    assert before[0] > before[1], f"没把列表拖宽：{before}"
    window.save_geometry()

    second = MainWindow(gui_paths)
    second.show()
    qapp.processEvents()
    assert second.splitter.sizes() == before, f"存 {before} 取 {second.splitter.sizes()}"
    second.quit_app()


def test_offscreen_geometry_gets_clamped_back(qapp, gui_paths):
    """在 4K 屏上调好的位置拿到笔记本上，窗口会开在屏幕外且再也拖不回来。"""
    from yiriyishi.core.config import load_settings, save_settings

    s = load_settings(gui_paths)
    s.window_geometry = {"x": 99999, "y": 99999, "w": 900, "h": 600, "maximized": False}
    save_settings(gui_paths, s)

    w = MainWindow(gui_paths)
    w.show()
    qapp.processEvents()
    screen = _available_rect()
    assert screen.left() <= w.x() <= screen.right() - 120, f"窗口 x={w.x()}"
    assert screen.top() <= w.y() <= screen.bottom() - 60, f"窗口 y={w.y()}"
    assert w.width() <= screen.width()
    w.quit_app()


def test_ball_off_a_removed_monitor_comes_back(qapp, gui_paths):
    """悬浮球拖到第二块屏后拔掉显示器，存档坐标会把它永久藏掉。"""
    from yiriyishi.core.config import load_settings, save_settings

    s = load_settings(gui_paths)
    s.float_ball_enabled = True
    s.float_ball_pos = (99999, 99999)
    save_settings(gui_paths, s)

    w = MainWindow(gui_paths)
    w.set_ball_visible(True)   # 球只在显示时才落到存档坐标上
    screen = _available_rect()
    # QRect.right() = left + width - 1，所以内边界要 +1，否则合法贴边会被判越界
    assert screen.left() <= w.ball.x() <= screen.right() - w.ball.width() + 1, f"球 x={w.ball.x()}"
    assert screen.top() <= w.ball.y() <= screen.bottom() - w.ball.height() + 1, f"球 y={w.ball.y()}"
    w.quit_app()

# ---------------- 周期任务细则：排期锚点 / 结束日 / 每天重复提醒 ----------------
def test_recurrence_rules_are_hidden_until_the_task_repeats(qapp, window):
    form = TaskForm(window.t, _repo(window), PersonRepository(window.conn))
    form.show()
    qapp.processEvents()
    assert not form.rec_rules.isVisible(), "不重复的任务不该看到周期细则"
    form.recurrence.setCurrentIndex(form.recurrence.findData("daily"))
    qapp.processEvents()
    assert form.rec_rules.isVisible()


def test_recurrence_rules_round_trip_through_the_form(qapp, window):
    """勾了「从完成日算 + 每天提醒 + 有结束日」，保存后重新载入必须原样回来。"""
    repo = _repo(window)
    form = TaskForm(window.t, repo, PersonRepository(window.conn))
    form.title_edit.setText("周报细则")
    form.recurrence.setCurrentIndex(form.recurrence.findData("weekly:1,3,5"))
    form.rec_anchor.setCurrentIndex(form.rec_anchor.findData("completion"))
    form.rec_daily_remind.setChecked(True)
    form.rec_until_check.setChecked(True)
    form.rec_until_edit.setDate(QDate(2026, 11, 30))
    assert form.commit()

    stored = next(t for t in repo.list() if t.title == "周报细则")
    assert stored.recurrence_anchor == "completion"
    assert stored.repeat_reminder_daily is True
    assert stored.recurrence_until.date() == date(2026, 11, 30)

    fresh = TaskForm(window.t, repo, PersonRepository(window.conn))
    fresh.load_task(stored)
    assert fresh.rec_anchor.currentData() == "completion"
    assert fresh.rec_daily_remind.isChecked()
    assert fresh.rec_until_check.isChecked()
    assert fresh.rec_until_edit.date() == QDate(2026, 11, 30)


def test_one_off_task_never_inherits_the_recurrence_rules(qapp, window):
    """细则行藏着，但人不保证没碰过：改成「不重复」时三项规定要一起失效。"""
    repo = _repo(window)
    form = TaskForm(window.t, repo, PersonRepository(window.conn))
    form.title_edit.setText("一次性的")
    form.rec_daily_remind.setChecked(True)
    form.rec_until_check.setChecked(True)
    form.commit()
    stored = next(t for t in repo.list() if t.title == "一次性的")
    assert stored.recurrence is None
    assert stored.repeat_reminder_daily is False
    assert stored.recurrence_until is None

# ---------------- 晨间回顾：设置项 / 早报投递 / 点击定位逾期 ----------------
def test_settings_dialog_exposes_morning_review(qapp, window, monkeypatch):
    """早报要能关、时间要能改，而且点「确定」要落盘。"""
    from PySide6.QtCore import QTime
    from PySide6.QtWidgets import QDialogButtonBox

    from yiriyishi.core.config import load_settings
    from yiriyishi.ui import settings_dialog

    monkeypatch.setattr(settings_dialog.autostart, "is_enabled", lambda *a: False)
    monkeypatch.setattr(settings_dialog.autostart, "enable", lambda *a, **k: (True, ""))
    monkeypatch.setattr(settings_dialog.autostart, "disable", lambda *a, **k: (True, ""))

    dlg = settings_dialog.SettingsDialog(window.t, window.paths, window.settings,
                                         window.apply_theme_mode, window)
    assert dlg.review_enabled.isChecked(), "新功能是来帮忙的，默认就该开着"
    dlg.review_time.setTime(QTime(7, 30))
    box = dlg.findChild(QDialogButtonBox)
    box.button(QDialogButtonBox.StandardButton.Ok).click()

    saved = load_settings(window.paths)
    assert saved.morning_review_time == "07:30"
    assert saved.morning_review_enabled is True


def test_deliver_review_sends_exactly_one_notification(qapp, window):
    from yiriyishi.core.notify import Notification
    from yiriyishi.core.scheduler import REVIEW_TARGET, Reminder

    sent = []

    class Recording:
        supports_activation = True

        def notify(self, n):
            sent.append(n)
            return True

    window.notifier = Recording()
    window.deliver_review(Reminder(None, Notification(
        title="☀️ 晨间回顾", message="逾期 1 · 今日 2", task_id=REVIEW_TARGET)))
    assert [n.title for n in sent] == ["☀️ 晨间回顾"]


def test_clicking_the_review_notification_opens_the_overdue_filter(qapp, window):
    """早报的点击不是定位某条任务，而是把人直接放到「逾期」面前。"""
    from yiriyishi.core.scheduler import REVIEW_TARGET

    repo = _repo(window)
    repo.create(title="逾期甲", due_time=datetime.now() - timedelta(hours=5))
    repo.create(title="未来丙", due_time=datetime.now() + timedelta(days=3))
    window.focus_task(REVIEW_TARGET)
    titles = [window.list.item(i).data(ROLE_TASK).title for i in range(window.list.count())]
    assert titles == ["逾期甲"]


# ---------------- 子任务：详情面板的「子任务」Tab ----------------
def test_detail_panel_lists_subtasks_and_reports_progress(qapp, window):
    repo = _repo(window)
    parent = repo.create(title="搬家")
    repo.create(title="打包书房", parent_id=parent.id)
    repo.create(title="退租", parent_id=parent.id, status="done")
    window.reload()
    assert window.list.select_task(parent.id)
    panel = window.detail.subtasks
    assert panel.titles() == ["打包书房", "退租"]
    assert panel.progress_label.text() == "1/2"


def test_checking_a_subtask_finishes_it_but_not_the_parent(qapp, window):
    """父任务永远手动完成：勾完最后一项，它还得留在待办里。"""
    repo = _repo(window)
    parent = repo.create(title="搬家")
    child = repo.create(title="打包书房", parent_id=parent.id)
    window.reload()
    window.list.select_task(parent.id)
    panel = window.detail.subtasks
    panel.set_checked(0, True)
    assert repo.get(child.id).status == "done"
    assert repo.get(parent.id).status == "todo"
    assert panel.progress_label.text() == "1/1"


def test_deleting_a_subtask_row_removes_it(qapp, window):
    repo = _repo(window)
    parent = repo.create(title="搬家")
    child = repo.create(title="打包书房", parent_id=parent.id)
    window.reload()
    window.list.select_task(parent.id)
    panel = window.detail.subtasks
    panel.delete_row(0)
    assert repo.get(child.id) is None
    assert panel.titles() == []


def test_adding_a_subtask_asks_for_a_dialog_preset_with_the_parent(qapp, window, monkeypatch):
    repo = _repo(window)
    parent = repo.create(title="搬家")
    window.reload()
    window.list.select_task(parent.id)
    # open_new_subtask 里是模态 exec()，offscreen 下会把测试钉死，所以只看它收到什么
    opened = []
    monkeypatch.setattr(window, "open_new_subtask", opened.append)
    window.detail.subtasks.add_btn.click()
    assert opened == [parent.id]


def test_subtask_dialog_hides_recurrence_and_keeps_the_parent(qapp, window):
    """子任务不继承周期：一期套一期会滚出无穷多份。"""
    from yiriyishi.ui.task_form import TaskDialog

    repo = _repo(window)
    parent = repo.create(title="搬家")
    dlg = TaskDialog(window.t, repo, PersonRepository(window.conn), None, window)
    saved = []
    dlg.form.saved.connect(saved.append)
    dlg.form.set_parent(parent.id)
    assert dlg.form.recurrence.isHidden()
    dlg.form.title_edit.setText("打包书房")
    dlg.form.commit()
    child = repo.get(saved[0])
    assert child.parent_id == parent.id
    assert child.recurrence is None


def test_a_subtask_reminder_focuses_its_parent_row(qapp, window):
    """通知里点子任务，主列表却没有这一行 —— 必须落到父任务上。"""
    repo = _repo(window)
    parent = repo.create(title="搬家")
    child = repo.create(title="打包书房", parent_id=parent.id)
    window.focus_task(child.id)
    assert window.list.current_task().id == parent.id


# ---------------- 逾期横幅：一键顺延 ----------------
def _banner_buttons(banner):
    from PySide6.QtWidgets import QPushButton

    # isHidden 而不是 isVisible：父窗口没 show 时子控件的 isVisible 恒为 False
    return [b.text() for b in banner.findChildren(QPushButton) if not b.isHidden()]


def test_overdue_banner_offers_two_postpone_targets(qapp, window):
    banner = window.overdue_banner
    banner.refresh(3, datetime(2026, 10, 9, 9, 0))
    assert not banner.isHidden()
    assert "3 项已逾期" in banner.text.text()
    texts = _banner_buttons(banner)
    assert "全部顺延到今晚 18:00" in texts
    assert "全部顺延到明天 09:00" in texts


def test_overdue_banner_drops_the_evening_target_after_18(qapp, window):
    """晚上 8 点再给「今晚 18:00」就是把任务往回推，等于没顺延。"""
    banner = window.overdue_banner
    banner.refresh(3, datetime(2026, 10, 9, 20, 0))
    assert "全部顺延到今晚 18:00" not in _banner_buttons(banner)


def test_overdue_banner_hides_when_nothing_is_overdue(qapp, window):
    banner = window.overdue_banner
    banner.refresh(2, datetime(2026, 10, 9, 9, 0))
    banner.refresh(0, datetime(2026, 10, 9, 9, 0))
    assert banner.isHidden()


def test_postpone_all_clears_every_overdue_task(qapp, window):
    repo = _repo(window)
    repo.create(title="逾期甲", due_time=datetime.now() - timedelta(hours=5))
    repo.create(title="逾期乙", due_time=datetime.now() - timedelta(hours=2))
    repo.create(title="未来丙", due_time=datetime.now() + timedelta(days=3))
    window.reload()
    assert not window.overdue_banner.isHidden()

    target = datetime.now() + timedelta(days=1)
    window.postpone_all_overdue(target)
    assert repo.stats(datetime.now())["overdue"] == 0
    assert window.overdue_banner.isHidden()
    jia = next(t for t in repo.list() if t.title == "逾期甲")
    assert jia.postponed_until == target.replace(microsecond=0)


def test_dismissing_the_overdue_banner_keeps_it_hidden(qapp, window):
    repo = _repo(window)
    repo.create(title="逾期甲", due_time=datetime.now() - timedelta(hours=5))
    window.reload()
    window.overdue_banner.findChild(QPushButton, "Dismiss").click()
    assert window.overdue_banner.isHidden()
    window.reload()
    assert window.overdue_banner.isHidden(), "本次运行内别再弹回来"


# ---------------- 子任务：主列表只显父行，副行带进度 ----------------
def test_parent_row_shows_subtask_progress_in_its_subline(qapp, window):
    """「只显父行 + 进度」：进度是子任务在主列表里唯一的露面方式。"""
    from yiriyishi.ui.task_list import ROLE_SUB

    repo = _repo(window)
    parent = repo.create(title="搬家")
    repo.create(title="打包书房", parent_id=parent.id, status="done")
    repo.create(title="退租", parent_id=parent.id)
    repo.create(title="买牛奶")
    window.reload()
    row = next(window.list.item(i) for i in range(window.list.count())
               if window.list.item(i).data(ROLE_TASK).title == "搬家")
    plain = next(window.list.item(i) for i in range(window.list.count())
                 if window.list.item(i).data(ROLE_TASK).title == "买牛奶")
    assert "✓ 1/2" in row.data(ROLE_SUB)
    assert "✓" not in plain.data(ROLE_SUB)


def test_overdue_filter_includes_the_parent_of_a_late_subtask(qapp, window):
    """子任务到期而父任务排在两周后 —— 父行必须出现在「逾期」里，不然它会隐身。"""
    from yiriyishi.core.scheduler import REVIEW_TARGET

    repo = _repo(window)
    parent = repo.create(title="搬家", due_time=datetime.now() + timedelta(days=14))
    repo.create(title="打包书房", parent_id=parent.id,
                due_time=datetime.now() - timedelta(hours=2))
    window.focus_task(REVIEW_TARGET)
    titles = [window.list.item(i).data(ROLE_TASK).title for i in range(window.list.count())]
    assert titles == ["搬家"]
