from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout,
)

from ..core.models import Person
from ..core.priority import Bucket, BUCKET_LABELS

SORT_LABELS = {
    "priority": "按优先级",
    "due": "按到期时间",
    "created": "按创建时间",
    "owner": "按负责人",
}


class FilterBar(QFrame):
    category_changed = Signal(object)   # Bucket | None(全部)
    search_changed = Signal(str)
    owner_changed = Signal(object)      # str | None
    sort_changed = Signal(str)

    def __init__(self, tokens, parent=None):
        super().__init__(parent)
        self.t = tokens
        self.setObjectName("Card")
        self._chips: dict[object, QPushButton] = {}
        self._owner_person_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # 第一行：分类 chip；第二行：搜索 + 人员 + 排序。单行挤在一起会显得局促。
        chips = QHBoxLayout()
        chips.setSpacing(6)
        for key in [None, Bucket.OVERDUE, Bucket.TODAY, Bucket.RECURRING, Bucket.FUTURE, Bucket.DONE]:
            label = "全部" if key is None else BUCKET_LABELS[key]
            btn = QPushButton(label)
            btn.setObjectName("CategoryChip")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._pick(k))
            self._chips[key] = btn
            chips.addWidget(btn)
        chips.addStretch(1)
        root.addLayout(chips)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题…")
        self.search.textChanged.connect(self.search_changed.emit)
        tools.addWidget(self.search, 1)

        self.owner = QComboBox()
        self.owner.setFixedWidth(140)
        self.owner.currentIndexChanged.connect(self._on_owner_index)
        tools.addWidget(self.owner)

        self.sort = QComboBox()
        self.sort.setFixedWidth(120)
        for mode, label in SORT_LABELS.items():
            self.sort.addItem(label, mode)
        self.sort.currentIndexChanged.connect(
            lambda _: self.sort_changed.emit(self.sort.currentData())
        )
        tools.addWidget(self.sort)
        root.addLayout(tools)
        self._reserve_chip_widths()
        self._pick(None)

    def _reserve_chip_widths(self) -> None:
        """每个 chip 先占好最宽文案的位置。

        条数跟着 30 秒一次的 reload 变，文案一长一短整行就左右跳格；选中态还是粗体，
        比常规更宽，所以按「文案 + 三位数 + 粗体」量。
        """
        bold = QFont(self.font())
        bold.setBold(True)
        for key, btn in self._chips.items():
            label = "全部" if key is None else BUCKET_LABELS[key]
            btn.setFont(bold)
            btn.setText(f"{label} 888")
            btn.ensurePolished()
            btn.setMinimumWidth(btn.sizeHint().width())
            btn.setText(label)
            btn.setFont(self.font())

    def set_categories(self, counts: dict[Bucket, int]) -> None:
        """把每个分类的条数写进 chip 文案，节简界面无需额外统计区。"""
        for key, btn in self._chips.items():
            label = "全部" if key is None else BUCKET_LABELS[key]
            n = counts.get(key, 0) if key is not None else sum(counts.values())
            btn.setText(f"{label} {n}" if n else label)

    def set_persons(self, persons: list[Person], keep: str | None = None) -> None:
        self.owner.blockSignals(True)
        self.owner.clear()
        self.owner.addItem("全部人员", None)
        for p in persons:
            self.owner.addItem(p.name, p.id)
        idx = self.owner.findData(keep if keep is not None else self._owner_person_id)
        self.owner.setCurrentIndex(idx if idx >= 0 else 0)
        self._owner_person_id = self.owner.currentData()
        self.owner.blockSignals(False)

    def set_sort(self, mode: str) -> None:
        idx = self.sort.findData(mode)
        if idx >= 0:
            self.sort.setCurrentIndex(idx)

    def current_category(self):
        for key, btn in self._chips.items():
            if btn.isChecked():
                return key
        return None

    def _pick(self, key) -> None:
        for k, btn in self._chips.items():
            btn.setProperty("active", k == key)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.category_changed.emit(key)

    def _on_owner_index(self, _idx: int) -> None:
        self._owner_person_id = self.owner.currentData()
        self.owner_changed.emit(self._owner_person_id)
