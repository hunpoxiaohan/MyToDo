from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget


BANNER_H = 60


class ToastBanner(QFrame):
    """主窗口顶部滑入的应用内提醒条，带「推迟 15 分钟」快捷操作。"""

    postpone_requested = Signal(str)
    open_requested = Signal(str)

    def __init__(self, tokens, parent: QWidget | None = None):
        super().__init__(parent)
        self.t = tokens
        self.setObjectName("ToastBanner")
        self._task_id: str | None = None
        self._visible = False
        self._collapsing = False
        self.setFixedHeight(0)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        self.icon = QLabel("⏰")
        self.text = QLabel("")
        self.text.setWordWrap(False)
        self.text.setStyleSheet(f"background:transparent; color:{self.t.c('text')};")
        lay.addWidget(self.icon)
        lay.addWidget(self.text, 1)
        postpone = QPushButton("推迟 15 分钟")
        postpone.setCursor(Qt.CursorShape.PointingHandCursor)
        postpone.clicked.connect(lambda: self._fire(self.postpone_requested))
        lay.addWidget(postpone)
        open_btn = QPushButton("查看")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: self._fire(self.open_requested))
        lay.addWidget(open_btn)
        close = QPushButton("×")
        close.setStyleSheet("border:none; background:transparent;")
        close.clicked.connect(self.hide_banner)
        lay.addWidget(close)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_banner)

        self._anim = QPropertyAnimation(self, b"maximumHeight", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_animated)

    def show_message(self, text: str, task_id: str | None, seconds: int = 12) -> None:
        self._task_id = task_id
        self._visible = True
        self.setVisible(True)
        self.text.setText(text)
        self._slide(BANNER_H)
        self._timer.start(seconds * 1000)

    def hide_banner(self) -> None:
        self._task_id = None
        self._visible = False
        self._timer.stop()
        self._slide(0)

    @property
    def visible_task_id(self) -> str | None:
        return self._task_id if self._visible else None

    def _fire(self, signal) -> None:
        if self._task_id:
            signal.emit(self._task_id)
        self.hide_banner()

    def _slide(self, height: int) -> None:
        self._collapsing = height == 0
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(height)
        self._anim.start()

    def _on_animated(self) -> None:
        if self._collapsing:
            self.setMaximumHeight(0)
            self.setVisible(False)
