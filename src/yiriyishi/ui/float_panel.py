from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..core import priority
from ..core.models import Task
from ..core.priority import BUCKET_LABELS, Bucket

BALL = 48
PANEL_WIDTH = 340
PANEL_RADIUS = 14
CLICK_MOVE_THRESHOLD = 4


class FloatBall(QWidget):
    """桌面悬浮球：实心小圆，右上角红点为「逾期 + 今日」条数。

    移动超过 4px 判定拖拽，否则算点击 → 展开/收起分类面板。
    边缘平滑靠 alpha，可点范围靠 mask：两者缺一不可——只有 alpha 会留下一个
    看不见却吞点击与滚轮的矩形，只有 mask 则圆边全是台阶。
    """

    clicked = Signal()                 # 单击（非拖拽）→ 由主窗切换面板开合
    position_changed = Signal(int, int)

    BODY = QRect(3, 3, BALL - 8, BALL - 8)
    BADGE = QRect(BALL - 22, 1, 20, 20)

    def __init__(self, tokens, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint
                        | Qt.WindowType.WindowStaysOnTopHint
                        | Qt.WindowType.Tool)
        self.t = tokens
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(BALL, BALL)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("一日一事：点击查看任务分类")
        self._count = 0
        self._drag_from: QPoint | None = None
        self._moved = False

    def set_count(self, n: int) -> None:
        if n != self._count:
            self._count = n
            self._apply_mask()
            self.update()

    def resizeEvent(self, event) -> None:
        self._apply_mask()
        super().resizeEvent(event)

    def _apply_mask(self) -> None:
        # 比画出来的形状大一圈，把抗锯齿的那 1px 留在窗口内，否则圆边被切成台阶
        body = QRegion(self.BODY.adjusted(-2, -2, 2, 2), QRegion.RegionType.Ellipse)
        if self._count > 0:
            body = body.united(QRegion(self.BADGE.adjusted(-1, -1, 1, 1),
                                       QRegion.RegionType.Ellipse))
        self.setMask(body)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(self.t.c("surface")), 2))
        p.setBrush(QColor(self.t.c("accent")))
        p.drawEllipse(self.BODY)

        p.setPen(QPen(QColor(self.t.c("accent_text")), 2, Qt.PenStyle.SolidLine,
                     Qt.PenCapStyle.RoundCap))
        # 三行清单图标：画出来而不是用 ☰，缺字体会渲染成方框
        for index, y in enumerate((18, 24, 30)):
            width = 18 if index < 2 else 12
            p.drawLine(QPoint(15, y), QPoint(15 + width, y))

        if self._count > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(self.t.c("danger")))
            p.drawEllipse(self.BADGE)
            small = QFont()
            small.setPointSizeF(8.5)
            small.setBold(True)
            p.setFont(small)
            p.setPen(QColor(self.t.c("accent_text")))
            p.drawText(self.BADGE, Qt.AlignCenter,
                       str(self._count) if self._count < 100 else "99+")
        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._moved = False
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.frameGeometry().topLeft() - self._drag_from
            if not self._moved and delta.manhattanLength() < CLICK_MOVE_THRESHOLD:
                return
            self._moved = True
            self.move(event.globalPosition().toPoint() - self._drag_from)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        dragged = self._moved
        self._drag_from = None
        self._moved = False
        if dragged:
            self.position_changed.emit(self.x(), self.y())
        else:
            self.clicked.emit()


class FloatPanel(QFrame):
    """点悬浮球后展开的分类概览：逾期 / 今日 / 周期 / 未来，每条一行摘要。

    行本身可点（唤起主窗并定位），行尾「完成」可就地清掉，省去「点开→回主窗→完成」的往返。
    卡片本体不透明；圆角外那圈透明像素用 mask 排除在窗口之外，
    免得看不见的角落替背后的窗口把点击和滚轮吃掉。
    """

    task_activated = Signal(str)
    task_completed = Signal(str)
    new_requested = Signal()
    open_main_requested = Signal()
    dismiss = Signal()

    MAX_ROWS = 5

    def __init__(self, tokens, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.t = tokens
        self.setObjectName("FloatPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(PANEL_WIDTH)
        self._rows: list[QWidget] = []
        self._build_skeleton()

    def resizeEvent(self, event) -> None:
        self.setMask(_rounded_region(self.rect().adjusted(-1, -1, 1, 1), PANEL_RADIUS))
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        """卡片自己画：不靠样式表，才能保证本体永远实心不透明、圆角带抗锯齿。"""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(self.t.c("hover")), 1))
        p.setBrush(QColor(self.t.c("surface")))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), PANEL_RADIUS, PANEL_RADIUS)
        p.end()
        super().paintEvent(event)

    def _build_skeleton(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(4)
        title = QLabel("一日一事")
        title.setStyleSheet(f"font-weight:700; color:{self.t.c('text')}; background:transparent;")
        head.addWidget(title)
        head.addStretch(1)
        for text, handler in (("+ 新任务", self.new_requested.emit),
                              ("主窗口", self.open_main_requested.emit),
                              ("×", self.dismiss.emit)):
            btn = QPushButton(text)
            btn.setObjectName("Ghost")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(handler)
            head.addWidget(btn)
        outer.addLayout(head)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("background:transparent;")
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 2, 6, 0)
        self._list_lay.setSpacing(5)
        self._scroll.setWidget(self._list_host)
        outer.addWidget(self._scroll)
        self.setMaximumHeight(460)

    def render(self, groups: dict[Bucket, list[tuple[Task, priority.PriorityResult, str]]]) -> None:
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._rows.clear()

        shown_any = False
        for bucket in (Bucket.OVERDUE, Bucket.TODAY, Bucket.RECURRING, Bucket.FUTURE):
            rows = groups.get(bucket) or []
            if not rows:
                continue
            shown_any = True
            header = QLabel(f"{BUCKET_LABELS[bucket]} · {len(rows)}")
            header.setObjectName("SectionTitle")
            header.setStyleSheet("margin-top:6px;")
            self._list_lay.addWidget(header)
            for task, result, owner in rows[: self.MAX_ROWS]:
                self._list_lay.addWidget(self._row(task, result, owner))
            if len(rows) > self.MAX_ROWS:
                more = QLabel(f"…另有 {len(rows) - self.MAX_ROWS} 条，去主窗口查看全部")
                more.setObjectName("Muted")
                self._list_lay.addWidget(more)

        if not shown_any:
            empty = QLabel("今日无待办")
            empty.setStyleSheet(
                f"color:{self.t.c('accent')}; background:transparent; padding:20px; "
                f"font-size:14px; font-weight:600;"
            )
            self._list_lay.addWidget(empty)
        self._list_lay.addStretch(1)
        self.adjustSize()

    def _row(self, task: Task, result: priority.PriorityResult, owner: str) -> QWidget:
        row = QWidget()
        row.setObjectName("PanelRow")
        row.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.mousePressEvent = lambda _e, tid=task.id: self.task_activated.emit(tid)  # type: ignore[assignment]

        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setSpacing(8)

        dot = QLabel()
        dot.setPixmap(_dot_pixmap(self.t.level(result.level.value),
                                 self.devicePixelRatioF()))
        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)

        done = QPushButton("完成")
        done.setObjectName("RowDone")
        done.setFixedHeight(24)
        done.setToolTip("就地完成；周期任务自动滚到下一期")
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.clicked.connect(lambda _=False, tid=task.id: self.task_completed.emit(tid))

        # 面板宽度固定：按可用像素省略号截断，而不是按字数——否则中文会被按钮切成半个字
        avail = (PANEL_WIDTH - 28 - 16 - 9 - 16 - done.sizeHint().width())
        title_font = QFont(self.font())
        title_font.setPixelSize(13)
        title_font.setBold(True)
        meta_font = QFont(self.font())
        meta_font.setPixelSize(12)

        box = QVBoxLayout()
        box.setSpacing(1)
        title = QLabel(QFontMetrics(title_font).elidedText(
            task.title, Qt.TextElideMode.ElideRight, avail))
        title.setFont(title_font)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet(f"color:{self.t.c('text')};")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.addWidget(title)
        left = priority.humanize_left(result.minutes_left)
        meta = f"{owner} · {left}" if owner else left
        label = QLabel(QFontMetrics(meta_font).elidedText(
            meta, Qt.TextElideMode.ElideRight, avail))
        label.setObjectName("Muted")
        label.setFont(meta_font)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.addWidget(label)
        lay.addLayout(box, 1)

        lay.addWidget(done, 0, Qt.AlignmentFlag.AlignVCenter)

        self._rows.append(row)
        return row


def _rounded_region(rect: QRect, radius: int) -> QRegion:
    """PySide6 的 QRegion 没有 RoundedRectangle 类型，自己拼：横竖两条 + 四个角圆。"""
    diameter = radius * 2
    region = (QRegion(rect.adjusted(radius, 0, -radius, 0))
              .united(QRegion(rect.adjusted(0, radius, 0, -radius))))
    corners = ((rect.left(), rect.top()),
               (rect.right() - diameter + 1, rect.top()),
               (rect.left(), rect.bottom() - diameter + 1),
               (rect.right() - diameter + 1, rect.bottom() - diameter + 1))
    for x, y in corners:
        region = region.united(QRegion(QRect(x, y, diameter, diameter),
                                       QRegion.RegionType.Ellipse))
    return region


def _dot_pixmap(color: str, dpr: float = 1.0) -> QPixmap:
    """用画出来的圆点而不是 ● 字符：部分字体下会渲染成方框。"""
    size = 9
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(0, 0, size, size)
    p.end()
    return pm
