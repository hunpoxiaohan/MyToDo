from __future__ import annotations

import argparse
import logging
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .core.autostart import enable as autostart_enable
from .core.autostart import is_enabled as autostart_is_enabled
from .core.config import load_settings
from .core.db import connect, init_db
from .core.log import setup_logging
from .core.notify import make_notifier
from .core.scheduler import SchedulerThread
from .paths import Paths
from .ui.main_window import MainWindow
from .ui.single_instance import SingleInstance
from .ui.theme import apply_theme, resolve_mode, tokens

log = logging.getLogger("yiriyishi")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="yiriyishi", description="一日一事")
    ap.add_argument("--datadir", help="自定义数据目录（本地调试用）")
    ap.add_argument("--debug", action="store_true", help="控制台输出 DEBUG 日志")
    ap.add_argument("--minimized", action="store_true", help="启动即收进托盘")
    ap.add_argument("--smoke", action="store_true", help="构建界面后 3 秒自动退出（打包自检）")
    return ap.parse_args(argv if argv is not None else sys.argv[1:])


class ActivationBridge(QObject):
    """通知点击回调运行在非 GUI 线程，用信号把 task_id 送回主线程。"""

    activated = Signal(str)

    def request(self, task_id: str) -> None:
        self.activated.emit(task_id)


def build(argv: list[str] | None = None):
    """返回 (app, window, scheduler, single)；window 为 None 表示已有实例在跑。"""
    args = parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("一日一事")
    app.setOrganizationName("yiriyishi")

    paths = Paths(args.datadir)
    setup_logging(paths, debug=args.debug)
    log.info("数据目录：%s", paths.root)

    conn = connect(paths.db)
    init_db(conn)
    conn.close()

    single = SingleInstance(paths.root)
    if not single.acquire():
        log.info("已有实例在运行，本进程退出")
        return app, None, None, single

    settings = load_settings(paths)
    tokens_obj = tokens(resolve_mode(settings.theme_mode))
    apply_theme(app, tokens_obj)

    bridge = ActivationBridge()
    notifier = make_notifier(bridge.request)
    window = MainWindow(paths, notifier)
    bridge.activated.connect(window.focus_task)
    single.activate_requested.connect(window.show_main)

    scheduler = SchedulerThread(paths.db, lambda: window.settings)
    scheduler.due.connect(window.deliver_reminders)

    if args.minimized or settings.start_minimized:
        window.hide()
    else:
        window.show()
    window.set_ball_visible(settings.float_ball_enabled, persist=False)
    _heal_autostart(settings)

    if args.debug:
        window.status_label.setText(window.status_label.text() + f"  ·  数据：{paths.root}")
    if args.smoke:
        QTimer.singleShot(3000, window.quit_app)
    return app, window, scheduler, single


def _heal_autostart(settings) -> None:
    """自启路径在「开发模式 ↔ exe」切换后会失效，每次启动校正一次。"""
    if settings.autostart_intent and not autostart_is_enabled():
        ok, detail = autostart_enable()
        log.info("开机自启路径校正：%s %s", "成功" if ok else "失败", detail)


def main(argv: list[str] | None = None) -> int:
    app, window, scheduler, single = build(argv)
    if window is None:
        return 0
    scheduler.start()
    try:
        code = app.exec()
    finally:
        scheduler.stop()
        single.release()
        log.info("进程退出，code=%s", code)
    return code
