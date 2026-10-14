from __future__ import annotations

from datetime import datetime, time, timedelta

from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDateTimeEdit, QDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from ..core.models import Person, Task, new_id
from ..core.repository import PersonRepository, TaskRepository
from ..core.recurrence import complete_and_roll
from .note_timeline import NoteTimeline
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

# 业界两种锚点：OmniFocus 的 Regularly / From Completion，Todoist 的 every / every!
ANCHOR_OPTIONS = [
    ("schedule", "按日程排（错过不往后漂）"),
    ("completion", "从完成那天往后数"),
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
                 parent=None, modal: bool = False, note_repo=None):
        super().__init__(parent)
        self.t = tokens
        self.task_repo = task_repo
        self.person_repo = person_repo
        self._task_id: str | None = None
        self._parent_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        form = QFormLayout()
        self.form_layout = form
        # 左对齐：中文标签长短不一，右对齐会让标签列的右边缘参差，字段列看着没对齐
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setSpacing(8)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("任务标题")
        self.title_edit.setFont(ui_font(12, True))
        form.addRow("标题", self.title_edit)

        self.owner = OwnerCombo(person_repo.list())
        form.addRow("负责人", self.owner)

        # 完成时间只有四枚胶囊：先勾「有截止时间」再挑快捷按钮再改控件，
        # 那条路要先理解再动手，比四选一慢得多。
        due_row = QHBoxLayout()
        self.due_none = QPushButton("无")
        self.due_today = QPushButton("今天 18:00")
        self.due_tomorrow = QPushButton("明天 09:00")
        self.due_custom = QPushButton("自定义…")
        self.due_group = QButtonGroup(self)
        for index, capsule in enumerate((self.due_none, self.due_today,
                                         self.due_tomorrow, self.due_custom)):
            capsule.setObjectName("Capsule")
            capsule.setCheckable(True)
            self.due_group.addButton(capsule, index)
            due_row.addWidget(capsule)
        due_row.addStretch(1)
        form.addRow("完成时间", _wrap(due_row))
        self.due_group.idClicked.connect(self._apply_due_mode)

        self.due_edit = QDateTimeEdit()
        self.due_edit.setCalendarPopup(True)
        self.due_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.due_edit.setFont(mono_font(10))
        self.due_edit.setDateTime(datetime.now().replace(minute=0) + timedelta(hours=1))
        self.due_edit.setVisible(False)
        form.addRow("具体时间", self.due_edit)
        self.due_today.setChecked(True)

        self.recurrence = QComboBox()
        for value, label in RECURRENCE_OPTIONS:
            self.recurrence.addItem(label, value)
        form.addRow("周期", self.recurrence)

        # 周期细则单独成组：只有选了重复才现身，否则新任务对话框白占三行
        self.rec_rules = QWidget()
        self.rec_rules.setObjectName("RecurrenceRules")
        rules = QFormLayout(self.rec_rules)
        rules.setContentsMargins(0, 0, 0, 0)
        rules.setSpacing(8)
        rules.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        rules.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.rec_anchor = QComboBox()
        for value, label in ANCHOR_OPTIONS:
            self.rec_anchor.addItem(label, value)
        rules.addRow("排期", self.rec_anchor)

        until_row = QHBoxLayout()
        self.rec_until_check = QCheckBox("有结束日期")
        self.rec_until_edit = QDateEdit()
        self.rec_until_edit.setCalendarPopup(True)
        self.rec_until_edit.setDisplayFormat("yyyy-MM-dd")
        self.rec_until_edit.setFont(mono_font(10))
        self.rec_until_edit.setDate(QDate.currentDate().addDays(30))
        self.rec_until_check.toggled.connect(self.rec_until_edit.setEnabled)
        self.rec_until_edit.setEnabled(False)
        until_row.addWidget(self.rec_until_check)
        until_row.addWidget(self.rec_until_edit, 1)
        rules.addRow("结束", _wrap(until_row))

        self.rec_daily_remind = QCheckBox("没完成也每天到点提醒")
        rules.addRow("提醒", self.rec_daily_remind)
        self.rec_daily_remind.setToolTip(
            "重复的是提醒，不是任务：只要今天有这一期又没完成，到点再叫你一次。")

        form.addRow("", self.rec_rules)
        self.recurrence.currentIndexChanged.connect(self._toggle_rec_rules)
        self._toggle_rec_rules()

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
        self.note_edit.setMinimumHeight(84)
        root.addWidget(self.note_edit)

        # 记录同屏摆在备注下面：藏进第二个 Tab 就等于没做，新任务对话框里
        # 任务还不存在，记不了东西，这一整块干脆不建。
        self.notes = None
        self.records_label = None
        if note_repo is not None:
            self.records_label = QLabel("记录")
            self.records_label.setObjectName("SectionTitle")
            root.addWidget(self.records_label)
            self.notes = NoteTimeline(self.t, note_repo)
            self.notes.count_changed.connect(self._show_note_count)
            root.addWidget(self.notes, 1)

        # 按钮读法跟设置窗一致：删除单独撇在最左，确认动作成组靠右，取消收尾
        buttons = QHBoxLayout()
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(lambda: self.deleted.emit(self._task_id or ""))
        self.delete_btn.setVisible(False)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)

        self.save_btn = QPushButton("保存")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self.commit)
        buttons.addWidget(self.save_btn)
        self.complete_btn = QPushButton("完成")
        self.complete_btn.clicked.connect(self._complete)
        self.complete_btn.setVisible(False)
        buttons.addWidget(self.complete_btn)

        self.cancel_btn = None
        if modal:
            self.cancel_btn = QPushButton("取消")
            self.cancel_btn.clicked.connect(self.cancelled.emit)
            buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)

        self._apply_due_mode()

    # ---- public ----
    def set_parent(self, parent_id: str | None) -> None:
        """挂到某个父任务下：周期整组藏掉 —— 子任务一期套一期会滚出无穷多份。"""
        self._parent_id = parent_id
        show = parent_id is None
        self.recurrence.setVisible(show)
        label = self.form_layout.labelForField(self.recurrence)
        if label is not None:
            label.setVisible(show)
        self._toggle_rec_rules()

    def load_task(self, task: Task) -> None:
        self._task_id = task.id
        self.set_parent(task.parent_id)
        self.title_edit.setText(task.title)
        self.owner.setCurrentIndex(max(0, self.owner.findData(task.owner_id)))
        self._select_due(task.due_time)
        idx = self.recurrence.findData(task.recurrence)
        self.recurrence.setCurrentIndex(idx if idx >= 0 else 0)
        aidx = self.rec_anchor.findData(task.recurrence_anchor)
        self.rec_anchor.setCurrentIndex(aidx if aidx >= 0 else 0)
        self.rec_until_check.setChecked(task.recurrence_until is not None)
        if task.recurrence_until is not None:
            self.rec_until_edit.setDate(QDate(task.recurrence_until.year,
                                              task.recurrence_until.month,
                                              task.recurrence_until.day))
        self.rec_daily_remind.setChecked(task.repeat_reminder_daily)
        self._toggle_rec_rules()
        self.importance.setCurrentIndex(max(0, min(4, task.importance - 1)))
        ridx = self.reminder.findData(task.reminder_offset)
        self.reminder.setCurrentIndex(ridx if ridx >= 0 else 2)
        self.note_edit.setHtml(task.note or "")
        if self.notes is not None:
            self.notes.set_task(task.id)
        self.complete_btn.setVisible(True)
        self.delete_btn.setVisible(True)

    def clear_form(self) -> None:
        self._task_id = None
        self.set_parent(None)
        self.title_edit.clear()
        self.owner.setCurrentIndex(0)
        self._select_mode("due_today")
        self.recurrence.setCurrentIndex(0)
        self.rec_anchor.setCurrentIndex(0)
        self.rec_until_check.setChecked(False)
        self.rec_until_edit.setDate(QDate.currentDate().addDays(30))
        self.rec_daily_remind.setChecked(False)
        self.importance.setCurrentIndex(2)
        self.note_edit.clear()
        if self.notes is not None:
            self.notes.set_task(None)
        self.complete_btn.setVisible(False)
        self.delete_btn.setVisible(False)

    def _show_note_count(self, count: int) -> None:
        self.records_label.setText(f"记录 · {count}")

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
        due = self._due_value()
        existing = self.task_repo.get(self._task_id) if self._task_id else None
        task = existing or Task(id=self._task_id or new_id())
        task.title = title
        task.note = self.note_edit.toHtml()
        task.owner_id = owner_id
        task.due_time = due
        task.parent_id = self._parent_id
        task.recurrence = None if self._parent_id else self.recurrence.currentData()
        task.recurrence_anchor = (self.rec_anchor.currentData()
                                  if task.recurrence else "schedule")
        task.repeat_reminder_daily = (self.rec_daily_remind.isChecked()
                                      if task.recurrence else False)
        task.recurrence_until = self._until_value() if task.recurrence else None
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

    def _toggle_rec_rules(self) -> None:
        self.rec_rules.setVisible(self._parent_id is None
                                  and self._due_mode() != "due_none"
                                  and self.recurrence.currentData() is not None)

    def _until_value(self):
        if not self.rec_until_check.isChecked():
            return None
        day = self.rec_until_edit.date().toPython()
        return datetime.combine(day, time.min)

    # ---- 完成时间：四枚胶囊 ----
    _CAPSULES = ("due_none", "due_today", "due_tomorrow", "due_custom")

    def _due_mode(self) -> str:
        for name in self._CAPSULES:
            if getattr(self, name).isChecked():
                return name
        return "due_none"

    def _select_mode(self, mode: str) -> None:
        for name in self._CAPSULES:
            getattr(self, name).setChecked(name == mode)
        self._apply_due_mode()

    @staticmethod
    def _due_target(mode: str) -> datetime:
        """两枚预设胶囊落在哪：18:00 是「今天做完」，明天 09:00 是「明早第一件事」。"""
        now = datetime.now()
        if mode == "due_tomorrow":
            return (now + timedelta(days=1)).replace(hour=9, minute=0,
                                                     second=0, microsecond=0)
        return now.replace(hour=18, minute=0, second=0, microsecond=0)

    def _select_due(self, due: datetime | None) -> None:
        """任务上的完成时间翻译回胶囊，对不上预设就走自定义。"""
        if due is None:
            self._select_mode("due_none")
            return
        wanted = due.replace(second=0, microsecond=0)
        for mode in ("due_today", "due_tomorrow"):
            if wanted == self._due_target(mode):
                self._select_mode(mode)
                return
        self.due_edit.setDateTime(wanted)
        self._select_mode("due_custom")

    def _apply_due_mode(self, _id: int | None = None) -> None:
        mode = self._due_mode()
        self.due_edit.setVisible(mode == "due_custom")
        if mode in ("due_today", "due_tomorrow"):
            # 自定义控件始终跟着当前选择走，切来切去看到的和存下的才是同一个时间
            self.due_edit.setDateTime(self._due_target(mode))
        has_due = mode != "due_none"
        self.recurrence.setEnabled(has_due)
        self.reminder.setEnabled(has_due)
        self._toggle_rec_rules()

    def _due_value(self) -> datetime | None:
        """胶囊就是完成时间的唯一入口：预设自己算，自定义照控件。"""
        mode = self._due_mode()
        if mode == "due_none":
            return None
        if mode == "due_custom":
            return self.due_edit.dateTime().toPython().replace(microsecond=0)
        return self._due_target(mode)


class TaskDialog(QDialog):
    def __init__(self, tokens, task_repo, person_repo, task: Task | None = None, parent=None,
                 parent_id: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑任务" if task else ("新子任务" if parent_id else "新任务"))
        # 只锁宽度：显式最小高会盖掉布局算出的最小高，「保存」就被挤到窗口外面
        # 宽度下限由「完成时间」那一行四枚胶囊决定，再窄「自定义…」就出界了
        self.setMinimumWidth(520)
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
        elif parent_id:
            self.form.set_parent(parent_id)

    def _on_saved(self, _task_id: str) -> None:
        """保存 / 完成 / 删除都意味着这次编辑结束了。"""
        self.accept()


def _wrap(hlay: QHBoxLayout) -> QWidget:
    host = QWidget()
    # 布局放进子控件时 Qt 会带上默认边距，整行会比同列表单行缩进一截
    hlay.setContentsMargins(0, 0, 0, 0)
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
