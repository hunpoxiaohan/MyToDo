from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..core.models import STATUS_DONE, STATUS_TODO


class SubTaskPanel(QWidget):
    """父任务下的子任务清单：勾一下就算完成，改和删都在这一行里做完。

    子任务只在详情面板露面（主列表只显父行 + 进度），所以这里的每一次变更
    都要让窗口重算父任务的颜色和徽标 —— 通过 changed 冒出去。
    """

    changed = Signal()
    add_requested = Signal(str)      # 父任务 id
    edit_requested = Signal(str)     # 子任务 id

    def __init__(self, tokens, task_repo, parent=None):
        super().__init__(parent)
        self.t = tokens
        self.task_repo = task_repo
        self._task_id: str | None = None
        self._rows: list[tuple[QCheckBox, str]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(2)

        # 添加入口放在顶部：面板在滚动区里，放底部的话得先滚到看不见的地方才能加
        header = QHBoxLayout()
        self.add_btn = QPushButton("＋ 添加子任务")
        self.add_btn.setObjectName("Ghost")
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(
            lambda: self._task_id and self.add_requested.emit(self._task_id))
        header.addWidget(self.add_btn)
        header.addStretch(1)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color:{self.t.c('text_muted')}; border:none;")
        header.addWidget(self.progress_label)
        root.addLayout(header)

        self.list_host = QVBoxLayout()
        self.list_host.setSpacing(2)
        root.addLayout(self.list_host)
        root.addStretch(1)

        self.setVisible(False)

    def load(self, parent_id: str | None) -> None:
        self._task_id = parent_id
        self.setVisible(parent_id is not None)
        for i in reversed(range(self.list_host.count())):
            self.list_host.takeAt(i).widget().deleteLater()
        self._rows = []
        if parent_id is None:
            self.progress_label.setText("")
            return
        tasks = self.task_repo.subtasks(parent_id)
        for index, task in enumerate(tasks):
            self.list_host.addWidget(self._row(index, task))
        done = sum(1 for task in tasks if task.status == STATUS_DONE)
        self.progress_label.setText(f"{done}/{len(tasks)}" if tasks else "")

    def titles(self) -> list[str]:
        return [box.text() for box, _ in self._rows]

    def set_checked(self, index: int, checked: bool) -> None:
        task = self._task_at(index)
        if task is None:
            return
        task.status = STATUS_DONE if checked else STATUS_TODO
        task.completed_at = datetime.now() if checked else None
        self.task_repo.save(task)
        self.load(self._task_id)
        self.changed.emit()

    def delete_row(self, index: int) -> None:
        task = self._task_at(index)
        if task is None:
            return
        self.task_repo.delete(task.id)
        self.load(self._task_id)
        self.changed.emit()

    def _task_at(self, index: int):
        if 0 <= index < len(self._rows):
            return self.task_repo.get(self._rows[index][1])
        return None

    def _row(self, index: int, task) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        box = QCheckBox(task.title)
        box.setChecked(task.status == STATUS_DONE)
        if task.status == STATUS_DONE:
            # QSS 不支持 text-decoration，划掉做不到，退一步用浅字色表示「已完成」
            box.setStyleSheet(f"color:{self.t.c('text_muted')}; border:none;")
        box.toggled.connect(lambda state, i=index: self.set_checked(i, state))
        lay.addWidget(box, 1)

        when = task.scheduled_due()
        hint = QLabel(f"{when:%m-%d %H:%M}" if when else "未排期")
        hint.setStyleSheet(f"color:{self.t.c('text_muted')}; border:none;")
        lay.addWidget(hint)

        edit = QPushButton("改")
        edit.setObjectName("Ghost")
        edit.clicked.connect(lambda: self.edit_requested.emit(task.id))
        lay.addWidget(edit)

        remove = QPushButton("删")
        remove.setObjectName("Ghost")
        remove.clicked.connect(lambda i=index: self.delete_row(i))
        lay.addWidget(remove)
        self._rows.append((box, task.id))
        return row
