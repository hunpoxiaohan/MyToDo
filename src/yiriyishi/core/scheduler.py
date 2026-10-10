from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .models import AppSettings, Task, is_in_quiet_hours
from .notify import Notification
from .repository import TaskRepository

log = logging.getLogger("yiriyishi.core.scheduler")

CHECK_INTERVAL_MS = 30_000


@dataclass(frozen=True)
class Reminder:
    """一条到点的提醒：扫描线程只负责决定「该提醒谁」，发送留给 GUI 线程。"""

    task: Task
    notification: Notification


class ReminderChecker(QObject):
    """纯逻辑：给定 now 与设置，决定该提醒哪些任务。便于脱离线程单测。

    系统通知（WinRT toast）只能在 GUI 线程发送，所以这里不发，只返回结果。
    """

    def __init__(self, repo: TaskRepository, settings_provider):
        super().__init__()
        self.repo = repo
        self._settings_provider = settings_provider
        self.skipped_quiet: list[str] = []

    @property
    def settings(self) -> AppSettings:
        return self._settings_provider()

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

    due = Signal(list)  # list[Reminder]

    def __init__(self, db_path, settings_provider, parent: QObject | None = None):
        super().__init__(parent)
        self._db_path = db_path
        self._settings_provider = settings_provider
        self._timer: QTimer | None = None

    def run(self):
        from .db import connect

        conn = connect(self._db_path)
        try:
            checker = ReminderChecker(TaskRepository(conn), self._settings_provider)
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
        except Exception as exc:
            log.exception("调度扫描失败：%s", exc)
            return
        if fired:
            self.due.emit(fired)

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
