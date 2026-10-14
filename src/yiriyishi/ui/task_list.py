from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import (
    QAbstractItemModel, QModelIndex, QPoint, QRect, QSize, Qt, Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QStyle, QStyledItemDelegate, QTreeView,
)

from ..core import priority
from ..core.models import STATUS_DONE, Task
from .theme import ui_font

ROLE_TASK = Qt.ItemDataRole.UserRole          # Task
ROLE_RESULT = Qt.ItemDataRole.UserRole + 1    # PriorityResult
ROLE_OWNER = Qt.ItemDataRole.UserRole + 2     # 负责人姓名
ROLE_DUE = Qt.ItemDataRole.UserRole + 3       # 短日期标签（装载时算好，便于断言）
ROLE_PROGRESS = Qt.ItemDataRole.UserRole + 4  # 「2/5」，无子任务为空
ROLE_CHILD = Qt.ItemDataRole.UserRole + 5     # 是否子任务行

ROW_H = 46
RADIUS = 12
CARD_INSET = 8
CHEVRON_X = 4           # 展开箭头列：没有子任务也占着，好让所有勾选圈对齐
CIRCLE_X = 22
CIRCLE = 18
TITLE_X = 46
CHILD_INDENT = 20       # 子行整体右移，一眼看出层级
PLUS_W = 16
PROGRESS_W = 34
DUE_MIN_W, DUE_MAX_W = 40, 100

# 只有真会逼着人立刻处理的档位才画左色条，其余靠日期胶囊的颜色表达。
_LOUD = ("overdue", "urgent")

STATE_SELECTED = QStyle.StateFlag.State_Selected
STATE_MOUSE_OVER = QStyle.StateFlag.State_MouseOver

_FM9: QFontMetrics | None = None


def fm9() -> QFontMetrics:
    """右侧胶囊按像素量宽，不量就会互相压住。字体固定，量一次缓存起来。"""
    global _FM9
    if _FM9 is None:
        _FM9 = QFontMetrics(ui_font(9))
    return _FM9


def due_label(task: Task, now: datetime) -> str:
    return priority.format_due(task.effective_due(), now)


def progress_label(task: Task) -> str:
    return f"{task.subtask_done}/{task.subtask_total}" if task.subtask_total else ""


def row_for(task: Task, now: datetime, pset, owner: str = ""):
    """列表一行的五件套：任务、优先级、负责人、短日期、子任务进度。"""
    return (task, priority.compute_priority(task, now, pset), owner,
            priority.format_due(task.effective_due(), now), progress_label(task))


class _Node:
    """一行：任务加上它的孩子。"""

    __slots__ = ("task", "result", "owner", "due", "progress", "children", "parent", "row")

    def __init__(self, task, result, owner, due, progress, parent=None, row=0):
        self.task = task
        self.result = result
        self.owner = owner
        self.due = due
        self.progress = progress
        self.children: list[_Node] = []
        self.parent = parent
        self.row = row


class TaskTreeModel(QAbstractItemModel):
    """两层：顶层任务 + 它的子任务。折叠由控件管，模型只回答「有几个孩子」。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._roots: list[_Node] = []

    def build(self, rows, children_of: dict[str, list[Task]],
              now: datetime, pset) -> None:
        """rows 已排好序，每项是 (task, result, owner, due, progress)。"""
        self.beginResetModel()
        self._roots = []
        for row, (task, result, owner, due, progress) in enumerate(rows):
            node = _Node(task, result, owner, due, progress, row=row)
            for child in children_of.get(task.id, ()):
                node.children.append(_Node(
                    child, priority.compute_priority(child, now, pset), "",
                    priority.format_due(child.scheduled_due(), now), "",
                    parent=node, row=len(node.children)))
            self._roots.append(node)
        self.endResetModel()

    # ---- Qt 契约 ----
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        pnode = parent.internalPointer() if parent.isValid() else None
        nodes = self._roots if pnode is None else pnode.children
        if not (0 <= row < len(nodes)):
            return QModelIndex()
        return self.createIndex(row, column, nodes[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        node = index.internalPointer()
        if node is None or node.parent is None:
            return QModelIndex()
        return self.createIndex(node.parent.row, 0, node.parent)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if not parent.isValid():
            return len(self._roots)
        node = parent.internalPointer()
        return len(node.children) if node else 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        node = index.internalPointer()
        if node is None:
            return None
        return {
            ROLE_TASK: node.task,
            ROLE_RESULT: node.result,
            ROLE_OWNER: node.owner,
            ROLE_DUE: node.due,
            ROLE_PROGRESS: node.progress,
            ROLE_CHILD: node.parent is not None,
        }.get(role)

    # ---- 自家接口 ----
    def index_for(self, task_id: str) -> QModelIndex:
        for root in self._roots:
            if root.task.id == task_id:
                return self.index(root.row, 0)
            for child in root.children:
                if child.task.id == task_id:
                    return self.index(child.row, 0, self.index(root.row, 0))
        return QModelIndex()


class TaskDelegate(QStyledItemDelegate):
    def __init__(self, tokens, view, parent=None):
        super().__init__(parent)
        self.t = tokens
        self._view = view
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

    # ---- 几何：绘制和命中用同一套，改动只会一起改 ----
    @staticmethod
    def card_rect(option_rect: QRect) -> QRect:
        return option_rect.adjusted(CARD_INSET, 2, -CARD_INSET, -2)

    @staticmethod
    def indent_of(index) -> int:
        return CHILD_INDENT if index.data(ROLE_CHILD) else 0

    @classmethod
    def circle_rect(cls, option_rect: QRect, index) -> QRect:
        rect = cls.card_rect(option_rect)
        x = rect.left() + CIRCLE_X + cls.indent_of(index)
        return QRect(x, rect.center().y() - CIRCLE // 2, CIRCLE, CIRCLE)

    @classmethod
    def chevron_rect(cls, option_rect: QRect) -> QRect:
        rect = cls.card_rect(option_rect)
        return QRect(rect.left() + CHEVRON_X, rect.center().y() - 9, 16, 18)

    @staticmethod
    def due_w(text: str) -> int:
        if not text:
            return 0
        return max(DUE_MIN_W, min(DUE_MAX_W, fm9().horizontalAdvance(text) + 18))

    @classmethod
    def cluster_w(cls, index) -> int:
        """右侧一段的总宽：日期胶囊 [+ 进度] [＋]；子行没有 ＋。"""
        w = cls.due_w(index.data(ROLE_DUE) or "")
        if index.data(ROLE_PROGRESS):
            w += PROGRESS_W
        if not index.data(ROLE_CHILD):
            w += PLUS_W
        return w

    @classmethod
    def due_rect(cls, option_rect: QRect, index) -> QRect:
        rect = cls.card_rect(option_rect)
        w = cls.due_w(index.data(ROLE_DUE) or "")
        return QRect(rect.right() - w, rect.center().y() - 10, w - 4, 20)

    @classmethod
    def plus_rect(cls, option_rect: QRect, index) -> QRect:
        rect = cls.card_rect(option_rect)
        x = rect.right() - cls.due_w(index.data(ROLE_DUE) or "") - PLUS_W
        return QRect(x, rect.center().y() - 9, PLUS_W, 18)

    @classmethod
    def progress_rect(cls, option_rect: QRect, index) -> QRect:
        rect = cls.card_rect(option_rect)
        x = rect.right() - cls.due_w(index.data(ROLE_DUE) or "") - PROGRESS_W
        return QRect(x, rect.center().y() - 9, PROGRESS_W - 6, 18)

    # ---- 绘制 ----
    def paint(self, painter: QPainter, option, index) -> None:
        task = index.data(ROLE_TASK)
        result = index.data(ROLE_RESULT)
        if task is None or result is None:
            return super().paint(painter, option, index)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.card_rect(option.rect)
        child = bool(index.data(ROLE_CHILD))
        done = task.status == STATUS_DONE
        level = result.level.value
        color = QColor(self.t.level(level))
        progress = index.data(ROLE_PROGRESS) or ""
        owner = index.data(ROLE_OWNER) or ""
        due = index.data(ROLE_DUE) or ""

        self._paint_card(painter, rect, option)

        if not child and progress:
            self._paint_chevron(painter, self.chevron_rect(option.rect),
                                self._view.isExpanded(index))

        # 左侧色条只给父行：子任务本来就缩进一层，圆点也带着级别色，再来一条就成杂点
        if level in _LOUD and not done and not child:
            bar = QRect(rect.left() + self.indent_of(index), rect.top() + 9, 3,
                        rect.height() - 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color if level != "overdue" or self._flash_on
                             else _faded(color, 70))
            painter.drawRoundedRect(bar, 1.5, 1.5)

        self._paint_circle(painter, self.circle_rect(option.rect, index), task, color, done)

        # 标题：左边跟着缩进走，右边给日期/进度/＋ 让出整段宽度
        title_x = rect.left() + TITLE_X + self.indent_of(index)
        title_font = ui_font(10.5, bold=not done and not child)
        painter.setFont(title_font)
        painter.setPen(QColor(self.t.c("text") if not done else self.t.c("text_muted")))
        fm = QFontMetrics(title_font)
        owner_w = fm9().horizontalAdvance(owner) + 8 if owner else 0
        avail = max(40, rect.right() - self.cluster_w(index) - title_x - owner_w)
        painter.drawText(title_x, rect.top(), avail, rect.height(),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(task.title, Qt.TextElideMode.ElideRight, avail))
        if done:
            width = min(fm.horizontalAdvance(task.title), avail)
            painter.setPen(QColor(self.t.c("text_muted")))
            painter.drawLine(title_x, rect.center().y(), title_x + width, rect.center().y())

        cursor = rect.right() - self.due_w(due)
        if progress:
            cursor -= PROGRESS_W
            self._paint_pill(painter, self.progress_rect(option.rect, index),
                             progress, _faded(color, 130), self.t.c("text_muted"))
        if owner:
            cursor -= owner_w
            painter.setFont(ui_font(9))
            painter.setPen(QColor(self.t.c("text_muted")))
            painter.drawText(QRect(cursor, rect.top(), owner_w - 8, rect.height()),
                             Qt.AlignRight | Qt.AlignVCenter, owner)
        self._paint_pill(painter, self.due_rect(option.rect, index), due,
                         QColor(self.t.c("border")) if done else color,
                         self.t.c("text_muted") if done else self.t.level(level))
        if option.state & STATE_MOUSE_OVER and not child:
            painter.setFont(ui_font(11, bold=True))
            painter.setPen(QColor(self.t.c("text_muted")))
            painter.drawText(self.plus_rect(option.rect, index), Qt.AlignCenter, "＋")
        painter.restore()

    def _paint_card(self, painter, rect, option) -> None:
        # 行底：选中 > 悬停 > 常态，三级层次靠暖色深浅拉开
        if option.state & STATE_SELECTED:
            bg = QColor(self.t.c("accent_soft"))
        elif option.state & STATE_MOUSE_OVER:
            bg = QColor(self.t.c("surface_alt"))
        else:
            bg = QColor(self.t.c("surface"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, RADIUS, RADIUS)
        if option.state & STATE_SELECTED:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(self.t.c("accent")), 1.5))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), RADIUS - 1, RADIUS - 1)

    def _paint_circle(self, painter, box, task, color, done) -> None:
        """行首完成圈：空心=待办，实心打勾=已完成；重要度只给 4 星以上点一颗实心小点。"""
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if done:
            painter.setPen(QPen(color, 1.6))
            painter.setBrush(color)
            painter.drawEllipse(box)
            painter.setPen(QPen(QColor(self.t.c("accent_text")), 1.8,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            centre = box.center()
            painter.drawLine(centre + QPoint(-4, 0), centre + QPoint(-1, 3))
            painter.drawLine(centre + QPoint(-1, 3), centre + QPoint(5, -4))
            return
        painter.setPen(QPen(QColor(self.t.c("border")), 1.5))
        painter.drawEllipse(box)
        if task.importance >= 4:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(box.center(), 3, 3)

    def _paint_chevron(self, painter, box, expanded: bool) -> None:
        painter.setPen(QPen(QColor(self.t.c("text_muted")), 1.6,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        cx, cy = box.center().x(), box.center().y()
        if expanded:
            painter.drawLine(cx - 4, cy - 2, cx, cy + 3)
            painter.drawLine(cx, cy + 3, cx + 4, cy - 2)
        else:
            painter.drawLine(cx - 2, cy - 4, cx + 3, cy)
            painter.drawLine(cx + 3, cy, cx - 2, cy + 4)

    def _paint_pill(self, painter, box, text, edge, ink) -> None:
        if not text:
            return
        painter.setPen(QPen(edge, 1))
        painter.setBrush(_faded(edge, 26))
        radius = box.height() // 2
        painter.drawRoundedRect(box, radius, radius)
        painter.setFont(ui_font(9))
        painter.setPen(QColor(ink))
        painter.drawText(box, Qt.AlignCenter, text)


def _faded(color: QColor, alpha: int = 70) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


class TaskTree(QTreeView):
    """两层任务树：顶层任务 + 它的子任务，子任务不再藏在 Tab 里。

    父行默认摊开（藏起子任务正是这次要修的毛病），只记住用户手动折过的那几个。
    展开状态由控件自己攥着而不是交给视图：reload() 每分钟重置一次模型，
    视图那套按 QModelIndex 记的状态一重置就全丢了。
    """

    activated_task = Signal(str)
    status_menu_requested = Signal(str, QPoint)
    add_subtask_requested = Signal(str)
    collapsed_changed = Signal(object)      # 被折起来的父任务 id 集合

    def __init__(self, tokens, parent=None):
        super().__init__(parent)
        self.t = tokens
        self.model_ = TaskTreeModel(self)
        self.setModel(self.model_)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)     # 行高恒定，长列表滚动才不卡
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setIndentation(0)             # 缩进自己画，才轮得到 chevron 那一列
        self.setRootIsDecorated(False)
        self.setExpandsOnDoubleClick(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)        # 悬停高亮和「＋」都要 MouseOver 状态
        self.delegate = TaskDelegate(tokens, self, self)
        self.setItemDelegate(self.delegate)
        self._collapsed: set[str] = set()

    # ---- 数据 ----
    def load(self, rows, children_of: dict[str, list[Task]], now: datetime,
             pset, selected_id: str | None) -> None:
        keep_scroll = self.verticalScrollBar().value()
        self.model_.build(rows, children_of, now, pset)
        for row in range(self.model_.rowCount()):
            index = self.model_.index(row, 0)
            super().setExpanded(index, index.data(ROLE_TASK).id not in self._collapsed)
        if not (selected_id and self.select_task(selected_id)) and self.model_.rowCount():
            self.setCurrentIndex(self.model_.index(0, 0))
        self.verticalScrollBar().setValue(keep_scroll)

    def set_collapsed_ids(self, ids) -> None:
        self._collapsed = set(ids or ())

    def collapsed_ids(self) -> set[str]:
        return set(self._collapsed)

    # ---- 按可见行号说话（测试和命中判定都走这里）----
    def visible_rows(self) -> list[QModelIndex]:
        return [index for index, _ in self._flatten()]

    def task_at(self, row: int) -> Task | None:
        rows = self._flatten()
        return rows[row][0].data(ROLE_TASK) if 0 <= row < len(rows) else None

    def data_at(self, row: int, role):
        rows = self._flatten()
        return rows[row][0].data(role) if 0 <= row < len(rows) else None

    def row_of_task(self, task_id: str) -> int:
        for row, (_, node_id) in enumerate(self._flatten()):
            if node_id == task_id:
                return row
        return -1

    def row_rect(self, row: int) -> QRect:
        rows = self._flatten()
        return self.visualRect(rows[row][0]) if 0 <= row < len(rows) else QRect()

    def current_task(self) -> Task | None:
        index = self.currentIndex()
        return index.data(ROLE_TASK) if index.isValid() else None

    def currentRow(self) -> int:
        index = self.currentIndex()
        return -1 if not index.isValid() else self.row_of_task(index.data(ROLE_TASK).id)

    def _flatten(self) -> list[tuple[QModelIndex, str]]:
        out = []
        for row in range(self.model_.rowCount()):
            index = self.model_.index(row, 0)
            out.append((index, index.data(ROLE_TASK).id))
            if not self.isExpanded(index):
                continue
            for child_row in range(self.model_.rowCount(index)):
                child = self.model_.index(child_row, 0, index)
                out.append((child, child.data(ROLE_TASK).id))
        return out

    def select_task(self, task_id: str) -> bool:
        index = self.model_.index_for(task_id)
        if not index.isValid():
            return False
        parent = index.parent()
        if parent.isValid():
            # 定位到子任务时顺手摊开父行，但不改存档 —— 人家可能就是要折着
            super().setExpanded(parent, True)
        self.setCurrentIndex(index)
        self.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        return True

    # ---- 展开 ----
    def setExpanded(self, index, open_: bool) -> bool:
        """外部调用走这里：状态要同步进 _collapsed，reload 重置模型后才长得一样。"""
        result = super().setExpanded(index, open_)
        if index.isValid():
            self._track(index, open_)
        return result

    def _track(self, index, open_: bool) -> None:
        task_id = index.data(ROLE_TASK).id
        (self._collapsed.discard if open_ else self._collapsed.add)(task_id)

    def _flip(self, index) -> None:
        open_now = not self.isExpanded(index)
        super().setExpanded(index, open_now)
        self._track(index, open_now)
        self.collapsed_changed.emit(set(self._collapsed))

    # ---- 交互 ----
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        point = event.position().toPoint()
        index = self.indexAt(point)
        if not index.isValid():
            return super().mousePressEvent(event)
        box = self.visualRect(index)
        if TaskDelegate.circle_rect(box, index).contains(point):
            self.status_menu_requested.emit(
                index.data(ROLE_TASK).id, self.viewport().mapToGlobal(point))
            return
        if not index.data(ROLE_CHILD):
            if self.model_.rowCount(index) and TaskDelegate.chevron_rect(box).contains(point):
                self._flip(index)
                return
            if TaskDelegate.plus_rect(box, index).contains(point):
                self.add_subtask_requested.emit(index.data(ROLE_TASK).id)
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        point = event.position().toPoint()
        index = self.indexAt(point)
        if index.isValid() and not TaskDelegate.circle_rect(
                self.visualRect(index), index).contains(point):
            self.activated_task.emit(index.data(ROLE_TASK).id)
            return
        super().mouseDoubleClickEvent(event)
