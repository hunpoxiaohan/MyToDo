from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("yiriyishi.core.notify")

APP_ID = "com.yiriyishi.app"


@dataclass
class Notification:
    title: str
    message: str
    task_id: str | None = None
    audio: bool = True


class Notifier:
    """跨平台通知接口。supports_activation=False 的平台点击无法回传 task_id。"""

    supports_activation = False

    def notify(self, notification: Notification) -> bool:
        raise NotImplementedError


class WindowsToastsNotifier(Notifier):
    """windows-toasts：进程内 WinRT toast，点击回调可直接唤起窗口。"""

    supports_activation = True

    def __init__(self, on_activate: Callable[[str], None]):
        from windows_toasts import InteractableWindowsToaster, Toast, ToastDuration

        self._Toast = Toast
        self._duration = ToastDuration.Long
        self._on_activate = on_activate
        self._toaster = InteractableWindowsToaster("一日一事", APP_ID)
        self._register_aumid()

    @staticmethod
    def _register_aumid() -> None:
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception as exc:  # 权限受限时不影响发送
            log.warning("注册 AUMID 失败：%s", exc)

    def notify(self, notification: Notification) -> bool:
        callback = None
        if notification.task_id:
            task_id = notification.task_id

            def callback(_args, _tid=task_id):
                try:
                    self._on_activate(_tid)
                except Exception as exc:
                    log.error("通知点击回调失败：%s", exc)

        toast = self._Toast(
            text_fields=[notification.title, notification.message],
            duration=self._duration,
            on_activated=callback,
        )
        try:
            self._toaster.show_toast(toast)
            return True
        except Exception as exc:
            log.error("windows-toasts 发送失败：%s", exc)
            return False


class PlyerNotifier(Notifier):
    def notify(self, notification: Notification) -> bool:
        try:
            from plyer import notification as _plyer_notification

            _plyer_notification.notify(
                title=notification.title,
                message=notification.message,
                app_name="一日一事",
                timeout=10,
            )
            return True
        except Exception as exc:
            log.error("plyer 通知失败：%s", exc)
            return False


def make_notifier(on_activate: Callable[[str], None]) -> Notifier:
    if sys.platform == "win32":
        try:
            return WindowsToastsNotifier(on_activate)
        except Exception as exc:
            log.warning("windows-toasts 不可用，退回 plyer：%s", exc)
    return PlyerNotifier()
