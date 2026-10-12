from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class OverdueBanner(QFrame):
    """逾期堆积时的常驻横幅。

    逾期只藏在筛选 chip 里没人会主动去看，而「今天先做哪一件」正是一日一事的核心
    动作，所以这里给一个整批顺延的出口 —— 顺延后任务会在新时间点重新提醒。
    """

    postpone_all_requested = Signal(object)      # datetime：顺延到的时刻

    def __init__(self, tokens, parent=None):
        super().__init__(parent)
        self.t = tokens
        self.setObjectName("OverdueBanner")
        self._dismissed = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(8)
        self.icon = QLabel("⚠️")
        self.text = QLabel("")
        lay.addWidget(self.icon)
        lay.addWidget(self.text, 1)

        self.tonight_btn = QPushButton("全部顺延到今晚 18:00")
        self.tonight_btn.setObjectName("Ghost")
        self.tonight_btn.clicked.connect(lambda: self._fire(18, 0))
        lay.addWidget(self.tonight_btn)

        self.tomorrow_btn = QPushButton("全部顺延到明天 09:00")
        self.tomorrow_btn.setObjectName("Ghost")
        self.tomorrow_btn.clicked.connect(lambda: self._fire(9, 0, days=1))
        lay.addWidget(self.tomorrow_btn)

        dismiss = QPushButton("×")
        dismiss.setObjectName("Dismiss")
        dismiss.setStyleSheet("border:none; background:transparent;")
        dismiss.clicked.connect(self.dismiss)
        lay.addWidget(dismiss)

        for btn in (self.tonight_btn, self.tomorrow_btn, dismiss):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self.setVisible(False)

    def refresh(self, count: int, now: datetime) -> None:
        if count <= 0 or self._dismissed:
            self.setVisible(False)
            return
        self.text.setText(f"{count} 项已逾期 · 挑一件今天做掉，或整批顺延")
        # 过了 18:00 还提「今晚 18:00」等于把任务往回推，那不是顺延
        self.tonight_btn.setVisible(now.hour < 18)
        self.setVisible(True)

    def dismiss(self) -> None:
        self._dismissed = True      # 本次运行不再弹，下次启动会再提醒
        self.setVisible(False)

    def _fire(self, hour: int, minute: int, days: int = 0) -> None:
        target = (datetime.now() + timedelta(days=days)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        self.postpone_all_requested.emit(target)
