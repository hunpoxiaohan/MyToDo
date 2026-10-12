from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..core.models import TaskNote
from .theme import mono_font, ui_font

ROLE_NOTE = Qt.ItemDataRole.UserRole          # TaskNote
ROLE_DAY = Qt.ItemDataRole.UserRole + 1       # 分组头：值为该组日期，普通条目为 None

_WEEKDAYS = "周一 周二 周三 周四 周五 周六 周日".split()
TIME_COL = 42
ACTION_COL = 34


class _MultilineEdit(QPlainTextEdit):
    """Ctrl+Enter 提交、Esc 取消：记一条流水不该强迫去够按钮。"""

    submitted = Signal()
    cancelled = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.submitted.emit()
            return
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)


def _day_label(day: date) -> str:
    delta = (datetime.now().date() - day).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "昨天"
    return f"{day:%Y-%m-%d} {_WEEKDAYS[day.weekday()]}"


class NoteRow(QWidget):
    """一条记录：左边时分，右边正文可换行，改/删只在悬停或选中时露出。"""

    def __init__(self, tokens, note: TaskNote, on_save, on_delete, relayout, parent=None):
        super().__init__(parent)
        self.setObjectName("NoteRow")
        # QWidget 子类不自己画 QSS 背景，得显式开styled background
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.note = note
        self._on_save = on_save
        self._on_delete = on_delete
        self._relayout = relayout
        self._hovered = False
        self._selected = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(8)

        self.time_label = QLabel(note.created_at.strftime("%H:%M"))
        self.time_label.setObjectName("Muted")
        self.time_label.setFont(mono_font(9))
        self.time_label.setFixedWidth(TIME_COL)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.time_label)

        self.body_label = QLabel(note.body)
        self.body_label.setObjectName("NoteBody")
        self.body_label.setFont(ui_font(10))
        self.body_label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_label.setWordWrap(True)
        # 不给 TextSelectableByMouse：QLabel 一旦吃下鼠标事件，整行的选中/双击/悬停就全断了
        self.body_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.body_label, 1)

        self.editor = _MultilineEdit()
        self.editor.setObjectName("NoteEditor")
        self.editor.setFont(ui_font(10))
        self.editor.hide()
        self.editor.submitted.connect(self._commit)
        self.editor.cancelled.connect(self.end_edit)
        lay.addWidget(self.editor, 1)

        self.edit_btn = QPushButton("改")
        self.edit_btn.setObjectName("NoteAction")
        self.edit_btn.setFixedSize(26, 20)
        self.edit_btn.clicked.connect(self.begin_edit)
        self.del_btn = QPushButton("删")
        self.del_btn.setObjectName("NoteDelete")
        self.del_btn.setFixedSize(26, 20)
        self.del_btn.clicked.connect(lambda _=False: self._on_delete(self.note.id))
        for btn in (self.edit_btn, self.del_btn):
            btn.hide()
            lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignTop)

        tip = f"{note.created_at:%Y-%m-%d %H:%M} 添加"
        if note.updated_at:
            tip += f"\n{note.updated_at:%Y-%m-%d %H:%M} 修改"
        self.time_label.setToolTip(tip)
        self.body_label.setToolTip(note.body)

    # ---- 悬停 / 选中 ----
    def set_hovered(self, on: bool) -> None:
        self._hovered = on
        self._sync_actions()

    def set_selected(self, on: bool) -> None:
        self._selected = on
        self._sync_actions()

    def _sync_actions(self) -> None:
        show = (self._hovered or self._selected) and not self.editor.isVisibleTo(self)
        for btn in (self.edit_btn, self.del_btn):
            btn.setVisible(show)

    def enterEvent(self, event) -> None:
        self.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.set_hovered(False)
        super().leaveEvent(event)

    # ---- 原地编辑 ----
    def begin_edit(self) -> None:
        self.editor.setPlainText(self.note.body)
        self.editor.show()
        self.body_label.hide()
        self._sync_actions()
        self._relayout()
        self.editor.setFocus()

    def end_edit(self) -> None:
        self.editor.clear()
        self.editor.hide()
        self.body_label.show()
        self._sync_actions()
        self._relayout()

    def _commit(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            self.end_edit()
            return
        self._on_save(self.note.id, text)

    def body_width_for(self, row_width: int) -> int:
        return max(40, row_width - TIME_COL - ACTION_COL - 24)

    def height_for(self, row_width: int) -> int:
        """QListWidget 的 item 不会跟着 itemWidget 长高，只能自己算出这一行多高。"""
        width = self.body_width_for(row_width)
        self.body_label.setMinimumWidth(width)
        return max(30, 10 + self.body_label.heightForWidth(width))


class NoteTimeline(QWidget):
    """任务过程记录：上方快速输入，下方按天分组、组内最新在上。"""

    count_changed = Signal(int)

    def __init__(self, tokens, note_repo, parent=None):
        super().__init__(parent)
        self.t = tokens
        self.repo = note_repo
        self._task_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.hint = QLabel("")
        self.hint.setObjectName("SectionTitle")
        self.hint.hide()
        root.addWidget(self.hint)

        # 按钮贴在输入框右边：单独占一行会把列表挤得只剩一条记录的高度
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self.input = _MultilineEdit()
        self.input.setObjectName("NoteInput")
        self.input.setFont(ui_font(10))
        self.input.setPlaceholderText("记一条进展 / 卡点 / 结论（Ctrl+Enter 保存）")
        self.input.setFixedHeight(48)
        input_row.addWidget(self.input, 1)

        self.add_btn = QPushButton("记录")
        self.add_btn.setObjectName("Primary")
        self.add_btn.setFixedHeight(30)
        input_row.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(input_row)

        self.list = QListWidget()
        self.list.setObjectName("NoteList")
        self.list.setMinimumHeight(120)   # 三行左右：再少一行都显示不全一条记录
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setMouseTracking(True)
        root.addWidget(self.list, 1)

        # 控件全建好了再接信号，接完再动作才不会撞上半成品状态
        self.add_btn.clicked.connect(self.submit)
        self.input.submitted.connect(self.submit)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.itemDoubleClicked.connect(self._open_editor)

    # ---- public ----
    def set_task(self, task_id: str | None) -> None:
        self._task_id = task_id
        self.input.setEnabled(bool(task_id))
        self.add_btn.setEnabled(bool(task_id))
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        notes = self.repo.list(self._task_id) if self._task_id else []
        self._add_groups(notes)
        self.hint.setText(self._hint_for(len(notes)))
        self.hint.setVisible(bool(self.hint.text()))
        self.count_changed.emit(len(notes))
        self._sync_widths()

    def submit(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or not self._task_id:
            return
        self.repo.add(self._task_id, text)
        self.input.clear()
        self.refresh()
        self.list.setCurrentRow(0)

    # ---- internals ----
    def _hint_for(self, count: int) -> str:
        """条数已经写在 Tab 标题上，这里只在「没任务 / 还没记过」时说一句。"""
        if not self._task_id:
            return "先选一个任务，才能记东西"
        return "" if count else "还没有记录，上面这条就是第一条"

    def _add_groups(self, notes: list[TaskNote]) -> None:
        groups: dict[date, list[TaskNote]] = {}
        for note in notes:
            groups.setdefault(note.created_at.date(), []).append(note)
        for day in sorted(groups, reverse=True):
            header = QListWidgetItem(_day_label(day))
            header.setData(ROLE_DAY, day)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setFont(ui_font(9, True))
            header.setForeground(QColor(self.t.c("text_muted")))
            self.list.addItem(header)
            for note in reversed(groups[day]):
                item = QListWidgetItem()
                item.setData(ROLE_NOTE, note)
                self.list.addItem(item)
                self.list.setItemWidget(
                    item, NoteRow(self.t, note, self._save, self._delete, self._sync_widths))

    def _save(self, note_id: str, text: str) -> None:
        self.repo.update(note_id, text)
        self.refresh()

    def _delete(self, note_id: str) -> None:
        self.repo.delete(note_id)
        self.refresh()

    def _open_editor(self, item: QListWidgetItem) -> None:
        row = self.list.itemWidget(item)
        if isinstance(row, NoteRow):
            row.begin_edit()

    def _on_current_changed(self, current, _previous) -> None:
        for i in range(self.list.count()):
            row = self.list.itemWidget(self.list.item(i))
            if isinstance(row, NoteRow):
                row.set_selected(self.list.item(i) is current)

    def _sync_widths(self) -> None:
        width = self.list.viewport().width() or 260
        for i in range(self.list.count()):
            item = self.list.item(i)
            row = self.list.itemWidget(item)
            if not isinstance(row, NoteRow):
                continue
            row.setFixedWidth(width)
            height = 96 if row.editor.isVisibleTo(row) else row.height_for(width)
            item.setSizeHint(QSize(width, height))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_widths()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Tab 切回「记录」时宽度才第一次是真值，之前的 sizeHint 是拿默认宽算出来的
        self._sync_widths()
