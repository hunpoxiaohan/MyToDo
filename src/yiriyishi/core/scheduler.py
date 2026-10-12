from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .models import STATUS_TODO, AppSettings, Task, is_in_quiet_hours
from .notify import Notification
from .repository import MetaRepository, TaskRepository

log = logging.getLogger("yiriyishi.core.scheduler")

CHECK_INTERVAL_MS = 30_000

REVIEW_DATE_KEY = "last_morning_review"
# 早报通知的点击定位到「逾期」筛选，不对应任何单条任务；任务 id 是 32 位十六进制，撞不上
REVIEW_TARGET = "@overdue"


@dataclass(frozen=True)
class Reminder:
    """一条到点的提醒：扫描线程只负责决定「该提醒谁」，发送留给 GUI 线程。

    task 为 None 表示它不属于任何单条任务（晨间回顾）。
    """

    task: Task | None
    notification: Notification


class ReminderChecker(QObject):
    """纯逻辑：给定 now 与设置，决定该提醒哪些任务。便于脱离线程单测。

    系统通知（WinRT toast）只能在 GUI 线程发送，所以这里不发，只返回结果。
    """

    def __init__(self, repo: TaskRepository, settings_provider, meta: MetaRepository):
        super().__init__()
        self.repo = repo
        self._settings_provider = settings_provider
        self.meta = meta
        self.skipped_quiet: list[str] = []

    @property
    def settings(self) -> AppSettings:
        return self._settings_provider()

    def morning_review(self, now: datetime | None = None) -> Reminder | None:
        """一天一条早报：逾期几项、今天几项、最紧急的是哪件。

        只看时间、不看完成状态，所以到点就推 —— 和「没完成也每天提醒」同源。
        """
        cfg = self.settings
        now = now or datetime.now()
        if not cfg.morning_review_enabled or cfg.reminders_paused:
            return None
        if cfg.dnd_enabled and is_in_quiet_hours(now, cfg.dnd_start, cfg.dnd_end):
            return None     # 免打扰里不翻篇，时段结束后还能补上
        if now.time() < datetime.strptime(cfg.morning_review_time, "%H:%M").time():
            return None
        today = now.date().isoformat()
        if self.meta.get(REVIEW_DATE_KEY) == today:
            return None
        # 决定过就翻篇：即使此刻没待办，也不该在深夜补推一条
        self.meta.set(REVIEW_DATE_KEY, today)

        open_tasks = [t for t in self.repo.list() if t.status == STATUS_TODO]
        if not open_tasks:
            return None
        from . import priority

        head = priority.sort_tasks(open_tasks, now, "priority", cfg.priority)[0]
        stats = self.repo.stats(now)
        return Reminder(None, Notification(
            title="☀️ 晨间回顾",
            message=(f"逾期 {stats['overdue']} · 今日到期 {stats['due_today']} · "
                     f"待办 {stats['open']} · 最紧急：{head.title}"),
            task_id=REVIEW_TARGET,
            audio=False,
        ))

    def tick(self, now: datetime | None = None) -> list[Reminder]:
        cfg = self.settings
        now = now or datetime.now()
        if cfg.reminders_paused:
            return []
        fired: list[Reminder] = []
        quiet = cfg.dnd_enabled and is_in_quiet_hours(now, cfg.dnd_start, cfg.dnd_end)
        for task in self.repo.notification_candidates(now):
            if quiet:
                # 免打扰期间只标记不弹窗，避免时段结束后一次性轰炸
                self.repo.mark_notified(task.id, now)
                self.skipped_quiet.append(task.id)
                log.info("免打扰时段内静默提醒：%s", task.title)
                continue
            # 先标记再交给 GUI 线程：即使发送失败也不会每 30 秒无限重试
            self.repo.mark_notified(task.id, now)
            fired.append(Reminder(task, Notification(
                title=_title(task, now),
                message=_body(task, now),
                task_id=task.id,
            )))
        return fired


class SchedulerThread(QThread):
    """独立线程每 30s 扫描；Qt 事件循环让连接与定时器都活在该线程上。"""

    due = Signal(list)          # list[Reminder]
    review_due = Signal(object)  # Reminder：一天最多一条晨间早报

    def __init__(self, db_path, settings_provider, parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._settings_provider = settings_provider
        self._timer: QTimer | None = None

    def run(self):
        from .db import connect

        conn = connect(self._db_path)
        try:
            checker = ReminderChecker(TaskRepository(conn), self._settings_provider,
                                      MetaRepository(conn))
            self._timer = QTimer()
            self._timer.setInterval(CHECK_INTERVAL_MS)
            self._timer.timeout.connect(lambda: self._safe_tick(checker))
            self._timer.start()
            self._safe_tick(checker)  # 启动即扫一次，补上关机期间漏掉的提醒
            log.info("提醒调度线程启动，间隔 %ss", CHECK_INTERVAL_MS // 1000)
            self.exec()
        finally:
            if self._timer is not None:
                self._timer.stop()
            conn.close()
            log.info("提醒调度线程退出")

    def _safe_tick(self, checker: ReminderChecker):
        try:
            fired = checker.tick()
            review = checker.morning_review()
        except Exception as exc:
            log.exception("调度扫描失败：%s", exc)
            return
        if fired:
            self.due.emit(fired)
        if review is not None:
            self.review_due.emit(review)

    def stop(self):
        self.quit()
        self.wait(3000)


def _title(task: Task, now: datetime) -> str:
    due = task.effective_due()
    if due and due < now:
        return f"⏰ 已逾期 · {task.title}"
    return f"⏰ 待办提醒 · {task.title}"


def _body(task: Task, now: datetime) -> str:
    from .priority import humanize_left, compute_priority

    left = compute_priority(task, now).minutes_left
    return f"剩余：{humanize_left(left)}（第 {task.importance} 星优先级）"
