from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDateTimeEdit, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from ..core.models import Person, Task, new_id
from ..core.repository import PersonRepository, TaskRepository
from ..core.recurrence import complete_and_roll
from .theme import mono_font, ui_font

RECURRENCE_OPTIONS = [
    (None, "不重复"),
    ("daily", "每天"),
    ("weekly:1", "每周一"),
    ("weekly:1,3,5", "每周一 / 三 / 五"),
    ("monthly:1", "每月 1 号"),
    ("monthly:15", "每月 15 号"),
    ("every_n_days:3", "每 3 天"),
    ("every_n_days:7", "每 7 天"),
]

STAR_OPTIONS = [(i, "★" * i + "☆" * (5 - i)) for i in range(1, 6)]


class OwnerCombo(QComboBox):
    """可搜索的常用人员下拉，输入新名字可直接建人。"""

    def __init__(self, persons: list[Person], parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._persons = []
        self.set_persons(persons)

    def set_persons(self, persons: list[Person]) -> None:
        keep = self.currentData()
        self._persons = persons
        self.blockSignals(True)
        self.clear()
        self.addItem("（未指派）", None)
        for p in persons:
            self.addItem(p.name, p.id)
        idx = self.findData(keep)
        self.setCurrentIndex(idx if idx >= 0 else 0)
        self.blockSignals(False)

    def current_person_id(self) -> str | None:
        return self.currentData()

    def typed_new_name(self) -> str | None:
        """返回需要新建的人员名；命中已有人员则返回 None。"""
        if self.currentData() is not None:
            return None
        name = self.currentText().strip()
        return name or None


class TaskForm(QWidget):
    """新增/编辑合一的表单。对话框与右侧详情面板复用同一个类，避免两套字段漂移。"""

    saved = Signal(str)
    completed = Signal(str)
    deleted = Signal(str)
    cancelled = Signal()

    def __init__(self, tokens, task_repo: TaskRepository, person_repo: PersonRepository,
                 parent=None, modal: bool = False):
        super().__init__(parent)
        self.t = tokens
        self.task_repo = task_repo
        self.person_repo = person_repo
        self._task_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        head = QHBoxLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("任务标题")
        self.title_edit.setFont(ui_font(12, True))
        head.addWidget(QLabel("标题"))
        head.addWidget(self.title_edit, 1)
        root.addLayout(head)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.owner = OwnerCombo(person_repo.list())
        form.addRow("负责人", self.owner)

        due_row = QHBoxLayout()
        self.due_enable = QPushButton("有截止时间")
        self.due_enable.setCheckable(True)
        self.due_enable.setChecked(True)
        self.due_enable.toggled.connect(self._toggle_due)
        self.due_edit = QDateTimeEdit()
        self.due_edit.setCalendarPopup(True)
        self.due_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.due_edit.setFont(mono_font(10))
        self.due_edit.setDateTime(datetime.now().replace(minute=0) + timedelta(hours=1))
        due_row.addWidget(self.due_enable)
        due_row.addWidget(self.due_edit, 1)
        for label, offset in (("今天 18:00", None), ("明天 09:00", 1), ("本周五", 2)):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, spec=offset, text=label: self._quick_due(spec, text))
            due_row.addWidget(btn)
        form.addRow("完成时间", _wrap(due_row))

        self.recurrence = QComboBox()
        for value, label in RECURRENCE_OPTIONS:
            self.recurrence.addItem(label, value)
        form.addRow("周期", self.recurrence)

        self.importance = QComboBox()
        for value, label in STAR_OPTIONS:
            self.importance.addItem(label, value)
        self.importance.setCurrentIndex(2)
        form.addRow("重要度", self.importance)

        self.reminder = QComboBox()
        for minutes in (0, 5, 15, 30, 60, 1440):
            self.reminder.addItem(_reminder_label(minutes), minutes)
        self.reminder.setCurrentIndex(2)
        form.addRow("提前提醒", self.reminder)

        root.addLayout(form)

        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("备注（支持粘贴，保留简单排版）")
        self.note_edit.setMinimumHeight(120)
        root.addWidget(self.note_edit, 1)

        buttons = QHBoxLayout()
        self.save_btn = QPushButton("保存")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self.commit)
        buttons.addWidget(self.save_btn)
        self.complete_btn = QPushButton("完成")
        self.complete_btn.clicked.connect(self._complete)
        self.complete_btn.setVisible(False)
        buttons.addWidget(self.complete_btn)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.clicked.connect(lambda: self.deleted.emit(self._task_id or ""))
        self.delete_btn.setVisible(False)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)
        if modal:
            cancel = QPushButton("取消")
            cancel.clicked.connect(self.cancelled.emit)
            buttons.addWidget(cancel)
        root.addLayout(buttons)

        self._toggle_due(True)

    # ---- public ----
    def load_task(self, task: Task) -> None:
        self._task_id = task.id
        self.title_edit.setText(task.title)
        self.owner.setCurrentIndex(max(0, self.owner.findData(task.owner_id)))
        has_due = task.due_time is not None
        self.due_enable.setChecked(has_due)
        if has_due:
            self.due_edit.setDateTime(task.due_time)
        idx = self.recurrence.findData(task.recurrence)
        self.recurrence.setCurrentIndex(idx if idx >= 0 else 0)
        self.importance.setCurrentIndex(max(0, min(4, task.importance - 1)))
        ridx = self.reminder.findData(task.reminder_offset)
        self.reminder.setCurrentIndex(ridx if ridx >= 0 else 2)
        self.note_edit.setHtml(task.note or "")
        self.complete_btn.setVisible(True)
        self.delete_btn.setVisible(True)
        self._toggle_due(has_due)

    def clear_form(self) -> None:
        self._task_id = None
        self.title_edit.clear()
        self.owner.setCurrentIndex(0)
        self.due_edit.setDateTime(datetime.now().replace(minute=0) + timedelta(hours=1))
        self.recurrence.setCurrentIndex(0)
        self.importance.setCurrentIndex(2)
        self.note_edit.clear()
        self.complete_btn.setVisible(False)
        self.delete_btn.setVisible(False)

    def refresh_persons(self, persons: list[Person]) -> None:
        self.owner.set_persons(persons)

    def commit(self) -> bool:
        title = self.title_edit.text().strip()
        if not title:
            self.title_edit.setFocus()
            self.title_edit.setStyleSheet("border:1px solid %s;" % self.t.c("danger"))
            return False
        self.title_edit.setStyleSheet("")

        owner_id = self._resolve_owner()
        due = self.due_edit.dateTime().toPython().replace(microsecond=0) \
            if self.due_enable.isChecked() else None
        existing = self.task_repo.get(self._task_id) if self._task_id else None
        task = existing or Task(id=self._task_id or new_id())
        task.title = title
        task.note = self.note_edit.toHtml()
        task.owner_id = owner_id
        task.due_time = due
        task.recurrence = self.recurrence.currentData()
        task.reminder_offset = int(self.reminder.currentData())
        task.importance = int(self.importance.currentData())
        if task.recurrence and not task.recurrence_group_id:
            task.recurrence_group_id = task.id
        self.task_repo.save(task)
        self.saved.emit(task.id)
        return True

    def _complete(self) -> None:
        if not self._task_id:
            return
        task = self.task_repo.get(self._task_id)
        if task:
            complete_and_roll(self.task_repo, task, datetime.now())
        self.completed.emit(self._task_id)

    def _resolve_owner(self) -> str | None:
        person_id = self.owner.current_person_id()
        if person_id:
            return person_id
        name = self.owner.typed_new_name()
        if not name:
            return None
        created = self.person_repo.create(name)
        self.owner.set_persons(self.person_repo.list())
        self.owner.setCurrentIndex(max(0, self.owner.findData(created.id)))
        return created.id

    def _toggle_due(self, on: bool) -> None:
        self.due_edit.setEnabled(on)
        self.recurrence.setEnabled(on)
        self.reminder.setEnabled(on)
        self.due_enable.setText("有截止时间" if on else "无截止时间")

    def _quick_due(self, spec, label: str) -> None:
        now = datetime.now()
        if spec is None and label.startswith("今天"):
            target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        elif spec == 1:
            target = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        else:  # 本周五
            days_to_fri = (4 - now.weekday()) % 7
            target = (now + timedelta(days=days_to_fri)).replace(hour=18, minute=0, second=0, microsecond=0)
        self.due_enable.setChecked(True)
        self.due_edit.setDateTime(target)
        self._toggle_due(True)


class TaskDialog(QDialog):
    def __init__(self, tokens, task_repo, person_repo, task: Task | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑任务" if task else "新任务")
        self.setMinimumSize(460, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.form = TaskForm(tokens, task_repo, person_repo, self, modal=True)
        lay.addWidget(self.form)
        self.form.saved.connect(self._on_saved)
        self.form.deleted.connect(self._on_saved)
        self.form.completed.connect(self._on_saved)
        self.form.cancelled.connect(self.reject)
        if task:
            self.form.load_task(task)


def _wrap(hlay: QHBoxLayout) -> QWidget:
    host = QWidget()
    host.setLayout(hlay)
    return host


def _reminder_label(minutes: int) -> str:
    if minutes == 0:
        return "到期时"
    if minutes < 60:
        return f"提前 {minutes} 分钟"
    if minutes < 1440:
        return f"提前 {minutes // 60} 小时"
    return f"提前 {minutes // 1440} 天"
