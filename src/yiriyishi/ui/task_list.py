from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QListWidget, QListView, QListWidgetItem,
    QStyle, QStyledItemDelegate,
)

from ..core import priority
from ..core.models import Task

ROLE_TASK = Qt.UserRole          # Task 对象
ROLE_RESULT = Qt.UserRole + 1    # PriorityResult
ROLE_OWNER = Qt.UserRole + 2     # 负责人姓名
ROLE_SUB = Qt.UserRole + 3       # 副行文案（负责人 · 剩余时间 · 子任务进度）

ROW_H = 58
RADIUS = 12


def subline(task: Task, result: priority.PriorityResult, owner: str) -> str:
    parts = [owner] if owner else []
    parts.append(priority.humanize_left(result.minutes_left))
    if task.subtask_total:
        parts.append(f"✓ {task.subtask_done}/{task.subtask_total}")
    return " · ".join(parts)


class TaskDelegate(QStyledItemDelegate):
    def __init__(self, tokens, parent=None):
        super().__init__(parent)
        self.t = tokens
        self._flash_on = True
        self._flashing = False

    def set_flashing(self, flashing: bool) -> None:
        """有逾期任务时 1Hz 交替明暗；没有逾期任务就停在常亮态。"""
        self._flashing = flashing
        if not flashing:
            self._flash_on = True

    @property
    def flash_on(self) -> bool:
        return self._flash_on

    def tick(self) -> None:
        if self._flashing:
            self._flash_on = not self._flash_on

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), ROW_H)

    def paint(self, painter: QPainter, option, index) -> None:
        task: Task = index.data(ROLE_TASK)
        result = index.data(ROLE_RESULT)
        if task is None or result is None:
            return super().paint(painter, option, index)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(8, 3, -8, -3)

        level = result.level.value
        is_overdue = level == "overdue"
        color = QColor(self.t.level(level))

        # 行底：选中 > 悬停 > 常态，三级层次靠暖色深浅拉开
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovering = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            bg = QColor(self.t.c("accent_soft"))
        elif hovering:
            bg = QColor(self.t.c("surface_alt"))
        else:
            bg = QColor(self.t.c("surface"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, RADIUS, RADIUS)
        if selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(self.t.c("accent")), 1.5))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), RADIUS - 1, RADIUS - 1)

        # 左侧优先级色条（过期闪烁 = 交替透明度）
        bar = QRect(rect.left(), rect.top() + 8, 4, rect.height() - 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color if not (is_overdue and not self._flash_on) else _faded(color))
        painter.drawRoundedRect(bar, 2, 2)

        text_left = rect.left() + 16
        done = task.status == "done"

        # 标题
        title_font = QFont(self.t_font())
        title_font.setPointSizeF(10.5)
        title_font.setBold(not done)
        painter.setFont(title_font)
        painter.setPen(QColor(self.t.c("text")) if not done else QColor(self.t.c("text_muted")))
        fm = QFontMetrics(title_font)
        title_w = rect.right() - text_left - 96
        text = fm.elidedText(task.title, Qt.TextElideMode.ElideRight, max(title_w, 40))
        painter.drawText(text_left, rect.top() + 6, title_w, 20, Qt.AlignLeft | Qt.AlignVCenter, text)
        if done:
            strike = QRect(text_left, rect.top() + 17, min(fm.horizontalAdvance(task.title), title_w), 1)
            painter.setPen(QColor(self.t.c("text_muted")))
            painter.drawLine(strike.topLeft(), strike.topRight())

        # 副行：负责人 + 剩余时间 + 子任务进度（文案在 load 里算好，便于测试）
        sub_font = QFont(self.t_font())
        sub_font.setPointSizeF(9)
        painter.setFont(sub_font)
        painter.setPen(QColor(self.t.c("text_muted")))
        sub = index.data(ROLE_SUB) or ""
        painter.drawText(text_left, rect.top() + 28, title_w, 18, Qt.AlignLeft | Qt.AlignVCenter, sub)

        # 右侧：周期标记 + 星级
        right_font = QFont(self.t_font())
        right_font.setPointSizeF(9)
        painter.setFont(right_font)
        painter.setPen(QColor(self.t.c("text_muted")))
        badge = "⟳" if task.recurrence else ""
        stars = f"{'★' * task.importance}{'☆' * (5 - task.importance)}"
        painter.drawText(
            QRect(rect.right() - 96, rect.top(), 90, rect.height()),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{badge} {stars}",
        )
        painter.restore()

    def t_font(self):
        from .theme import ui_font

        return ui_font()


def _faded(color: QColor) -> QColor:
    c = QColor(color)
    c.setAlpha(70)
    return c


class TaskList(QListWidget):
    """任务列表：外部按 (task, result, owner) 灌入，已排好序。"""

    activated_task = Signal(str)
    context_task = Signal(str)

    def __init__(self, tokens, parent=None):
        super().__init__(parent)
        self.t = tokens
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)   # 悬停高亮需要 MouseOver 状态
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.delegate = TaskDelegate(tokens, self)
        self.setItemDelegate(self.delegate)
        self.itemDoubleClicked.connect(lambda item: self.activated_task.emit(item.data(ROLE_TASK).id))

    def load(self, ordered: list[tuple[Task, priority.PriorityResult, str]], selected_id: str | None) -> None:
        self.clear()
        for task, result, owner in ordered:
            item = QListWidgetItem()
            item.setData(ROLE_TASK, task)
            item.setData(ROLE_RESULT, result)
            item.setData(ROLE_OWNER, owner)
            item.setData(ROLE_SUB, subline(task, result, owner))
            item.setSizeHint(QSize(0, ROW_H))
            self.addItem(item)
            if task.id == selected_id:
                item.setSelected(True)
                self.setCurrentItem(item)

    def current_task(self) -> Task | None:
        item = self.currentItem()
        return item.data(ROLE_TASK) if item else None

    def select_task(self, task_id: str) -> bool:
        for i in range(self.count()):
            item = self.item(i)
            if item.data(ROLE_TASK).id == task_id:
                self.setCurrentItem(item)
                self.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False
