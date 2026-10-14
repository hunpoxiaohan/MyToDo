from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton,
    QScrollArea, QSplitter, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from ..core import priority
from ..core.config import load_settings, save_settings
from ..core.db import connect, init_db
from ..core.models import STATUS_DONE, STATUS_TODO, Task
from ..core.notify import Notifier
from ..core.priority import Bucket
from ..core.recurrence import complete_and_roll
from ..core.repository import NoteRepository, PersonRepository, TaskRepository
from ..core.scheduler import REVIEW_TARGET, Reminder
from ..paths import Paths, asset_dir
from .filter_bar import FilterBar
from .float_panel import BALL, FloatBall, FloatPanel
from .overdue_banner import OverdueBanner
from .settings_dialog import SettingsDialog
from .task_form import TaskDialog, TaskForm
from .task_list import TaskTree, row_for
from .theme import apply_theme, refresh_tokens, resolve_mode, tokens
from .toast_banner import ToastBanner

log = logging.getLogger("yiriyishi.ui.main_window")

REFRESH_INTERVAL_MS = 60_000   # 每分钟重算优先级，让颜色随时间自动升级
FLASH_INTERVAL_MS = 1000
LIST_MIN_WIDTH = 300           # 树里有缩进和右侧胶囊，再窄标题就整行省略了


def _usable_rect() -> QRect:
    """所有屏幕可用区的并集。只认主屏的话，摆在副屏上的窗口会被误判成越界。"""
    rect = QRect()
    for screen in QApplication.screens():
        rect = rect.united(screen.availableGeometry())
    return rect


def _clamped(x: int, y: int, w: int, h: int) -> tuple[int, int]:
    """把坐标拉回屏幕内：换过显示器后，存档位置能让窗口和悬浮球永久找不回来。"""
    r = _usable_rect()
    if r.isNull():
        return x, y
    return (min(max(x, r.left()), max(r.left(), r.right() - w + 1)),
            min(max(y, r.top()), max(r.top(), r.bottom() - h + 1)))


class MainWindow(QMainWindow):
    def __init__(self, paths: Paths, notifier: Notifier | None = None):
        super().__init__()
        # 关窗不退出：只有托盘菜单的「退出」才结束进程
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.paths = paths
        self.notifier = notifier
        self.settings = load_settings(paths)
        self.t = tokens(resolve_mode(self.settings.theme_mode))

        self.conn = connect(paths.db)
        init_db(self.conn)
        self.task_repo = TaskRepository(self.conn)
        self.person_repo = PersonRepository(self.conn)
        self.note_repo = NoteRepository(self.conn)

        self._category: Bucket | None = None
        self._owner: str | None = None
        self._search = ""
        self._sort = self.settings.default_sort
        self._panel_open = False
        self._force_quit_close = False

        self.setWindowTitle("一日一事")
        self.setWindowIcon(QIcon(str(asset_dir() / "app.png")))
        self.resize(1060, 800)   # 详情表单最小高 574，视口再矮就把「保存」挤出可视区

        self._build_ui()
        self._build_tray()
        self._restore_geometry()

        self._clock = QTimer(self, interval=REFRESH_INTERVAL_MS, timeout=self.reload)
        self._clock.start()
        self._flash = QTimer(self, interval=FLASH_INTERVAL_MS, timeout=self._tick_flash)
        self._flash.start()
        self._draft_timer = QTimer(self, singleShot=True, interval=2000, timeout=self._write_draft)

        self.reload()
        self._offer_draft_restore()

    # ------------------------------- UI 构建 -------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        # 顶部只放品牌与动作按钮，筛选条独占一张卡片，避免一行挤到底
        top = QHBoxLayout()
        top.setSpacing(8)
        brand = QLabel("一日一事")
        brand.setStyleSheet("font-size:16px; font-weight:700;")
        top.addWidget(brand)
        top.addStretch(1)
        settings_btn = QPushButton("设置")
        settings_btn.setObjectName("Ghost")
        settings_btn.clicked.connect(self.open_settings)
        top.addWidget(settings_btn)
        new_btn = QPushButton("+ 新任务")
        new_btn.setObjectName("Primary")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self.open_new_task)
        top.addWidget(new_btn)
        root.addLayout(top)

        self.filter = FilterBar(self.t)
        self.filter.category_changed.connect(self._on_category)
        self.filter.search_changed.connect(self._on_search)
        self.filter.owner_changed.connect(self._on_owner)
        # 先恢复再连信号：连完再 set_sort 会在 self.list 建好之前就触发
        # _on_sort → reload()，打包后没有控制台，只留下日志里的 AttributeError
        self.filter.set_sort(self._sort)
        self.filter.sort_changed.connect(self._on_sort)
        root.addWidget(self.filter)

        self.toast = ToastBanner(self.t)
        self.toast.postpone_requested.connect(self.postpone_task)
        self.toast.open_requested.connect(self.focus_task)
        root.addWidget(self.toast)

        self.overdue_banner = OverdueBanner(self.t)
        self.overdue_banner.postpone_all_requested.connect(self.postpone_all_overdue)
        root.addWidget(self.overdue_banner)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = TaskTree(self.t)
        self.list.setMinimumWidth(LIST_MIN_WIDTH)
        self.list.activated_task.connect(self.open_task_edit)
        self.list.status_menu_requested.connect(self._ask_status)
        self.list.add_subtask_requested.connect(self.open_new_subtask)
        self.list.collapsed_changed.connect(self._on_collapsed)
        self.list.selectionModel().currentChanged.connect(lambda *_: self._sync_detail())
        self.splitter.addWidget(self.list)

        self.detail = TaskForm(self.t, self.task_repo, self.person_repo, note_repo=self.note_repo)
        self.detail.setObjectName("Card")
        self.detail.saved.connect(lambda _id: (self._clear_draft(), self.reload()))
        self.detail.completed.connect(self._on_completed)
        self.detail.deleted.connect(self._on_deleted)
        self.detail.title_edit.textEdited.connect(lambda *_: self._draft_timer.start())
        self.detail.note_edit.textChanged.connect(lambda *_: self._draft_timer.start())

        # 详情表单的自然最小高有 500 多，直接进分栏会把窗口顶到 700 多，
        # 768 高的笔记本屏放不下；套一层滚动容器让溢出的部分自己滚。
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("DetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setWidget(self.detail)
        self.splitter.addWidget(self.detail_scroll)

        # 不用 setStretchFactor：它只在没有显式尺寸时参与分配，实测压不过最小宽，
        # 想要的 3:2 从来没生效过。比例改由 save_geometry 里的 splitter 尺寸维持。
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        root.addWidget(self.splitter, 1)

        self.status_label = QPushButton("")
        self.status_label.setObjectName("Muted")
        self.status_label.setEnabled(False)
        root.addWidget(self.status_label)

        self.setCentralWidget(central)

        self.ball = FloatBall(self.t)
        self.ball.clicked.connect(lambda: self._toggle_panel(not self._panel_open))
        self.ball.position_changed.connect(self._on_ball_moved)
        self.panel = FloatPanel(self.t)
        self.panel.task_activated.connect(self.focus_task)
        self.panel.status_menu_requested.connect(self._ask_status)
        self.panel.new_requested.connect(self.open_new_task)
        self.panel.open_main_requested.connect(self.show_main)
        self.panel.dismiss.connect(lambda: self._toggle_panel(False))

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        menu.addAction(QAction("显示主窗口", self, triggered=self.show_main))
        self.pause_action = QAction("暂停提醒", self, checkable=True)
        self.pause_action.setChecked(self.settings.reminders_paused)
        self.pause_action.toggled.connect(self._toggle_pause)
        menu.addAction(self.pause_action)
        self.ball_action = QAction("显示悬浮球", self, checkable=True)
        self.ball_action.setChecked(self.settings.float_ball_enabled)
        self.ball_action.toggled.connect(self.set_ball_visible)
        menu.addAction(self.ball_action)
        menu.addSeparator()
        menu.addAction(QAction("设置…", self, triggered=self.open_settings))
        menu.addAction(QAction("退出", self, triggered=self.quit_app))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._hinted = False

    # ------------------------------- 数据刷新 -------------------------------
    def reload(self) -> None:
        now = datetime.now()
        pset = self.settings.priority
        tasks = self.task_repo.list()
        names = {p.id: p.name for p in self.person_repo.list(active_only=False)}
        self.filter.set_persons(self.person_repo.list(), keep=self._owner)

        visible = [t for t in tasks if self._matches(t, now)]
        ordered = priority.sort_tasks(visible, now, self._sort, pset)
        rows, children_of = [], {}
        for t in ordered:
            rows.append(row_for(t, now, pset, names.get(t.owner_id or "", "")))
            if t.subtask_total:
                children_of[t.id] = self.task_repo.subtasks(t.id)
        selected = self.list.current_task()
        self.list.load(rows, children_of, now, pset,
                       selected.id if selected else self.settings.selected_task_id)

        counts: dict[Bucket, int] = {}
        for t in tasks:
            bucket = priority.bucket_of(t, now)
            counts[bucket] = counts.get(bucket, 0) + 1
        self.filter.set_categories(counts)
        self.overdue_banner.refresh(counts.get(Bucket.OVERDUE, 0), now)

        stats = self.task_repo.stats(now)
        self.status_label.setText(
            f"待办 {stats['open']} · 今日到期 {stats['due_today']} · "
            f"逾期 {stats['overdue']} · 今日完成 {stats['done_today']}"
        )
        self.ball.set_count(counts.get(Bucket.OVERDUE, 0) + counts.get(Bucket.TODAY, 0))
        if self._panel_open:
            self.panel.render(_groups(tasks, names, now, pset))

    def _matches(self, task: Task, now: datetime) -> bool:
        if self._search and self._search.lower() not in task.title.lower():
            return False
        if self._owner and task.owner_id != self._owner:
            return False
        if self._category is None:
            return True
        return priority.bucket_of(task, now) == self._category

    # ------------------------------- 交互 -------------------------------
    def open_new_task(self) -> None:
        # 面板是置顶窗，会把模态框压在下面继续吃掉点击；先收起来（和 show_main 一样）
        self._toggle_panel(False)
        dlg = TaskDialog(self.t, self.task_repo, self.person_repo, None, self)
        dlg.form.saved.connect(lambda _id: self.reload())
        dlg.exec()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.t, self.paths, self.settings, self.apply_theme_mode, self)
        dlg.exec()
        self.settings = load_settings(self.paths)
        self.apply_theme_mode(self.settings.theme_mode)
        self.set_ball_visible(self.settings.float_ball_enabled, persist=False)
        self.reload()

    def focus_task(self, task_id: str) -> None:
        """通知点击 / 面板点击 / 双击都会走这里：唤起主窗并定位。"""
        self.show_main()
        if task_id == REVIEW_TARGET:
            # 早报的点击定位的是「一批」任务，不是某一条
            self._category = Bucket.OVERDUE
            self._search = ""
            self.reload()
            return
        self._category = None
        self._search = ""
        self.reload()
        # 子任务如今是树上的一行，直接定位它自己（select_task 会顺手展开父行）
        self.list.select_task(task_id)

    def open_new_subtask(self, parent_id: str) -> None:
        self._toggle_panel(False)
        dlg = TaskDialog(self.t, self.task_repo, self.person_repo, None, self,
                         parent_id=parent_id)
        dlg.form.saved.connect(lambda _id: self.reload())
        dlg.exec()

    def open_task_edit(self, task_id: str) -> None:
        task = self.task_repo.get(task_id)
        if task is None:
            return
        self._toggle_panel(False)
        dlg = TaskDialog(self.t, self.task_repo, self.person_repo, task, self)
        dlg.form.saved.connect(lambda _id: self.reload())
        dlg.exec()
        self.reload()

    def postpone_all_overdue(self, when: datetime) -> None:
        """横幅上的整批顺延：走单条 postpone 的同一条通道，到新点会再提醒。"""
        now = datetime.now()
        ids = [t.id for t in self.task_repo.list() if priority.bucket_of(t, now) is Bucket.OVERDUE]
        for task_id in ids:
            self.task_repo.postpone(task_id, when)
        self.reload()
        self.toast.show_message(f"已把 {len(ids)} 项顺延到 {priority.format_due(when, now)}",
                                None, seconds=6)

    def deliver_review(self, review: Reminder) -> None:
        """晨间早报：系统通知是主通道，没有通知能力时退到应用内横幅。"""
        if self.notifier is None or not self.notifier.notify(review.notification):
            self.toast.show_message(review.notification.message, None, seconds=30)
        self.reload()

    def postpone_task(self, task_id: str) -> None:
        self.task_repo.postpone(task_id, datetime.now() + timedelta(minutes=15))
        self.reload()
        self.toast.show_message("已推迟 15 分钟，到期前会再次提醒", task_id, seconds=5)

    def _status_menu(self, task: Task) -> QMenu:
        """菜单只给反向动作：待办行只有「标记完成」，已完成行只有「重新打开」。

        挂到发起请求的控件上，面板是置顶窗，菜单跟着它才不会垫到下面。
        """
        menu = QMenu(self.sender() or self)
        if task.status == STATUS_DONE:
            menu.addAction("重新打开", lambda: self.reopen_task(task.id))
        else:
            menu.addAction("标记完成", lambda: self.complete_task(task.id))
        return menu

    def _ask_status(self, task_id: str, pos: QPoint) -> None:
        """改状态一律过一遍菜单：误点只会弹个菜单，点别处就什么都没发生。"""
        task = self.task_repo.get(task_id)
        if task is None:
            return
        self._status_menu(task).exec(pos)

    def reopen_task(self, task_id: str) -> None:
        task = self.task_repo.get(task_id)
        if task is None:
            return
        self.task_repo.reopen(task_id)
        self.reload()
        self.toast.show_message(f"已把「{task.title}」放回待办", task_id, seconds=4)

    def complete_task(self, task_id: str) -> None:
        """菜单里点了「标记完成」才走到这里（周期任务自动滚到下一期）。"""
        task = self.task_repo.get(task_id)
        if task is None:
            return
        complete_and_roll(self.task_repo, task, datetime.now())
        self._on_completed(task_id)
        self.toast.show_message(f"已完成「{task.title}」", task_id, seconds=4)

    def deliver_reminders(self, reminders: list[Reminder]) -> None:
        """扫描线程决定要提醒谁，真正发送留在 GUI 线程（WinRT toast 的限制）。"""
        if not reminders:
            return
        for item in reminders:
            if self.notifier is not None and not self.notifier.notify(item.notification):
                log.warning("系统通知发送失败，降级为应用内提醒：%s", item.task.title)
        head = reminders[0].task
        extra = f" 等 {len(reminders)} 条" if len(reminders) > 1 else ""
        self.toast.show_message(f"「{head.title}」快到时间了{extra}", head.id)

    def _on_completed(self, task_id: str) -> None:
        self._clear_draft()
        self.reload()

    def _on_deleted(self, task_id: str) -> None:
        self.task_repo.delete(task_id)
        self._clear_draft()
        self.reload()

    def _on_category(self, bucket) -> None:
        self._category = bucket
        self.reload()

    def _on_search(self, text: str) -> None:
        self._search = text
        self.reload()

    def _on_owner(self, owner_id) -> None:
        self._owner = owner_id
        self.reload()

    def _on_sort(self, mode: str) -> None:
        self._sort = mode
        self.settings.default_sort = mode
        save_settings(self.paths, self.settings)
        self.reload()

    def _toggle_pause(self, paused: bool) -> None:
        self.settings.reminders_paused = paused
        save_settings(self.paths, self.settings)
        self.tray.showMessage("一日一事", "提醒已暂停" if paused else "提醒已恢复",
                              QSystemTrayIcon.MessageIcon.Information, 3000)

    def apply_theme_mode(self, mode_setting: str) -> None:
        """深浅色热切换：原地刷新 token，控件持有的引用自动看到新色。"""
        self.settings.theme_mode = mode_setting
        refresh_tokens(self.t, resolve_mode(mode_setting))
        apply_theme(QApplication.instance(), self.t)
        self.reload()

    # ------------------------------- 悬浮球 -------------------------------
    def set_ball_visible(self, visible: bool, persist: bool = True) -> None:
        self.settings.float_ball_enabled = visible
        self.ball_action.setChecked(visible)
        if persist:
            save_settings(self.paths, self.settings)
        if visible:
            self.ball.move(*self._ball_pos())
            self.ball.show()
        else:
            self.ball.hide()
            self._toggle_panel(False)

    def _ball_pos(self) -> tuple[int, int]:
        """存档坐标要钳回屏幕内：拔掉副屏后球会永久看不见，也就再也点不回来。"""
        if self.settings.float_ball_pos:
            x, y = self.settings.float_ball_pos
            return _clamped(x, y, BALL, BALL)
        screen = QApplication.primaryScreen().availableGeometry()
        return screen.right() - BALL - 24, screen.bottom() - BALL - 120

    def _on_ball_moved(self, x: int, y: int) -> None:
        self.settings.float_ball_pos = (x, y)
        save_settings(self.paths, self.settings)

    def _toggle_panel(self, open_: bool) -> None:
        self._panel_open = bool(open_)
        if not self._panel_open:
            self.panel.hide()
            return
        names = {p.id: p.name for p in self.person_repo.list(active_only=False)}
        self.panel.render(_groups(self.task_repo.list(), names, datetime.now(),
                                  self.settings.priority))
        self._place_panel()
        self.panel.show()

    def _place_panel(self) -> None:
        # 用屏幕并集而不是主屏：球可以合法地待在副屏上，只认主屏会把面板拽回主屏
        screen = _usable_rect()
        self.panel.adjustSize()
        pw, ph = self.panel.width(), self.panel.height()
        x = self.ball.x() - pw - 10
        if x < screen.left():
            x = self.ball.x() + BALL + 10
        y = max(screen.top(), min(self.ball.y() - 40, screen.bottom() - ph))
        self.panel.move(x, y)

    # ------------------------------- 托盘 / 生命周期 -------------------------------
    def show_main(self) -> None:
        self._toggle_panel(False)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_main()

    def closeEvent(self, event) -> None:
        """关窗 = 收进托盘，进程继续跑；只有托盘「退出」才真退。"""
        event.ignore()
        self.save_geometry()
        self.hide()
        self._toggle_panel(False)
        if not self._hinted:
            self._hinted = True
            self.tray.showMessage(
                "一日一事 仍在运行",
                "关掉的只是窗口，提醒依旧生效。要退出请在托盘菜单选「退出」。",
                QSystemTrayIcon.MessageIcon.Information, 4000,
            )

    def quit_app(self) -> None:
        # 先停表：否则关连接后还可能有 tick 打到已关闭的库
        self._clock.stop()
        self._flash.stop()
        self._draft_timer.stop()
        self.save_geometry()
        self._clear_draft()
        self.ball.hide()
        self.panel.hide()
        self.tray.hide()
        self.conn.close()
        QApplication.instance().quit()

    def save_geometry(self) -> None:
        self.settings.window_geometry = {
            "x": self.x(), "y": self.y(), "w": self.width(), "h": self.height(),
            "maximized": self.isMaximized(),
            "splitter": list(self.splitter.sizes()),
            "collapsed": sorted(self.list.collapsed_ids()),
        }
        self.settings.selected_task_id = (
            self.list.current_task().id if self.list.current_task() else None
        )
        save_settings(self.paths, self.settings)

    def _on_collapsed(self, ids) -> None:
        """折了哪个父行随手存：reload() 每分钟重置模型，靠这份名单才长得回同一棵树。"""
        self.settings.window_geometry = {**(self.settings.window_geometry or {}),
                                         "collapsed": sorted(ids)}
        save_settings(self.paths, self.settings)

    def _restore_geometry(self) -> None:
        g = self.settings.window_geometry
        screen = _usable_rect()
        if g:
            w = min(max(g.get("w", 1060), 720), max(screen.width(), 720))
            h = min(max(g.get("h", 800), 480), max(screen.height(), 480))
            self.setGeometry(*_clamped(g.get("x", 80), g.get("y", 80), w, h), w, h)
            sizes = g.get("splitter")
            if sizes and len(sizes) == 2 and sum(sizes) > 0:
                self.splitter.setSizes(sizes)
            self.list.set_collapsed_ids(g.get("collapsed", ()))
        if g and g.get("maximized"):
            self.showMaximized()

    def _sync_detail(self) -> None:
        task = self.list.current_task()
        if task is None:
            return
        self.settings.selected_task_id = task.id
        self.detail.load_task(task)

    # ------------------------------- 崩溃草稿 -------------------------------
    def _write_draft(self) -> None:
        task = self.list.current_task()
        if task is None:
            return
        payload = {
            "task_id": task.id,
            "title": self.detail.title_edit.text(),
            "note": self.detail.note_edit.toHtml(),
        }
        try:
            self.paths.draft.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.warning("草稿写入失败：%s", exc)

    def _offer_draft_restore(self) -> None:
        draft = _read_draft(self.paths.draft)
        if not draft:
            return
        if self.list.select_task(draft["task_id"]):
            self.detail.title_edit.setText(draft["title"])
            self.detail.note_edit.setHtml(draft["note"])
            self.toast.show_message("已恢复上次未保存的编辑", draft["task_id"], seconds=8)

    def _clear_draft(self) -> None:
        self.paths.draft.unlink(missing_ok=True)

    def _tick_flash(self) -> None:
        """仅当存在逾期任务时才做 1Hz 闪烁重绘，空闲时不消耗 CPU。"""
        now = datetime.now()
        overdue = any(
            t.status == STATUS_TODO and t.effective_due() and t.effective_due() < now
            for t in self.task_repo.list(collapse_recurring=False)
        )
        self.list.delegate.set_flashing(overdue)
        self.list.delegate.tick()
        if overdue:
            self.list.viewport().update()


def _groups(tasks: list[Task], names: dict[str, str], now: datetime, pset) -> dict:
    out: dict[Bucket, list] = {}
    open_tasks = priority.sort_tasks([t for t in tasks if t.status == STATUS_TODO],
                                     now, "priority", pset)
    for task in open_tasks:
        bucket = priority.bucket_of(task, now)
        result = priority.compute_priority(task, now, pset)
        out.setdefault(bucket, []).append((task, result, names.get(task.owner_id or "", "")))
    return out


def _read_draft(path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("task_id") else None
