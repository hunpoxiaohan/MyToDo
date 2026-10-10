from __future__ import annotations

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTimeEdit,
    QVBoxLayout, QWidget,
)

from ..core import autostart
from ..core.config import save_settings
from ..core.models import AppSettings, PrioritySettings
from ..core.sync import LocalBackupProvider


class SettingsDialog(QDialog):
    """设置：优先级阈值 / 免打扰 / 主题 / 自启 / 悬浮球 / 备份。"""

    def __init__(self, tokens, paths, settings: AppSettings, on_theme_change, parent=None):
        super().__init__(parent)
        self.t = tokens
        self.paths = paths
        self.settings = settings
        self.on_theme_change = on_theme_change
        self.setWindowTitle("设置")
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # ---- 优先级阈值 ----
        box_priority = QGroupBox("优先级阈值（按完成日期自动判级）")
        form = QFormLayout(box_priority)
        p = settings.priority
        self.urgent = QSpinBox()
        self.urgent.setRange(5, 720)
        self.urgent.setSuffix(" 分钟")
        self.urgent.setValue(p.urgent_minutes)
        form.addRow("当天任务剩多久算紧急（红）", self.urgent)

        self.warning = QSpinBox()
        self.warning.setRange(1, 24)
        self.warning.setSuffix(" 小时")
        self.warning.setValue(p.warning_hours)
        form.addRow("当天任务剩多久算临近（黄）", self.warning)

        self.near = QSpinBox()
        self.near.setRange(1, 30)
        self.near.setSuffix(" 天")
        self.near.setValue(p.near_days)
        form.addRow("跨天任务剩多久算临近（黄）", self.near)

        self.week = QSpinBox()
        self.week.setRange(2, 90)
        self.week.setSuffix(" 天")
        self.week.setValue(p.week_days)
        form.addRow("跨天任务剩多久算本周内（浅绿）", self.week)
        root.addWidget(box_priority)

        # ---- 提醒 ----
        box_dnd = QGroupBox("提醒与免打扰")
        form_dnd = QFormLayout(box_dnd)
        self.dnd_enabled = QCheckBox("启用免打扰时段（期间只记录不弹窗）")
        self.dnd_enabled.setChecked(settings.dnd_enabled)
        form_dnd.addRow(self.dnd_enabled)
        times = QHBoxLayout()
        self.dnd_start = QTimeEdit(QTime.fromString(settings.dnd_start, "HH:mm"))
        self.dnd_start.setDisplayFormat("HH:mm")
        self.dnd_end = QTimeEdit(QTime.fromString(settings.dnd_end, "HH:mm"))
        self.dnd_end.setDisplayFormat("HH:mm")
        times.addWidget(self.dnd_start)
        times.addWidget(QLabel("至"))
        times.addWidget(self.dnd_end)
        times.addStretch(1)
        form_dnd.addRow("静默时段（支持跨午夜）", _wrap(times))
        root.addWidget(box_dnd)

        # ---- 外观 ----
        box_ui = QGroupBox("外观与启动")
        form_ui = QFormLayout(box_ui)
        self.theme = QComboBox()
        for value, label in (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色")):
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme_mode)))
        self.theme.currentIndexChanged.connect(lambda _: self._preview_theme())
        form_ui.addRow("主题", self.theme)

        self.float_ball = QCheckBox("显示桌面悬浮球")
        self.float_ball.setChecked(settings.float_ball_enabled)
        form_ui.addRow(self.float_ball)

        self.autostart_box = QCheckBox("开机自动启动")
        self.autostart_box.setChecked(autostart.is_enabled())
        form_ui.addRow(self.autostart_box)
        root.addWidget(box_ui)

        # ---- 数据 / 备份 ----
        box_data = QGroupBox("数据与联网备份")
        form_data = QFormLayout(box_data)
        row = QHBoxLayout()
        self.backup_dir = QLineEdit(settings.backup_dir or "")
        self.backup_dir.setPlaceholderText("例如 D:\\Sync\\yiriyishi（可指向坚果云/OneDrive 目录）")
        browse = QPushButton("选择…")
        browse.clicked.connect(self._pick_backup_dir)
        row.addWidget(self.backup_dir, 1)
        row.addWidget(browse)
        form_data.addRow("备份目录", _wrap(row))
        status = "上次备份：" + ("成功" if settings.last_backup_ok else "失败/未执行")
        self.backup_status = QLabel(status)
        self.backup_status.setObjectName("Muted")
        form_data.addRow(self.backup_status)
        root.addWidget(box_data)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _preview_theme(self) -> None:
        self.on_theme_change(self.theme.currentData())

    def _pick_backup_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择备份目录", self.backup_dir.text())
        if chosen:
            self.backup_dir.setText(chosen)

    def _accept(self) -> None:
        s = self.settings
        s.priority = PrioritySettings(
            urgent_minutes=self.urgent.value(),
            warning_hours=self.warning.value(),
            near_days=self.near.value(),
            week_days=self.week.value(),
        )
        s.dnd_enabled = self.dnd_enabled.isChecked()
        s.dnd_start = self.dnd_start.time().toString("HH:mm")
        s.dnd_end = self.dnd_end.time().toString("HH:mm")
        s.theme_mode = self.theme.currentData()
        s.float_ball_enabled = self.float_ball.isChecked()
        s.backup_dir = self.backup_dir.text().strip() or None
        s.autostart_intent = self.autostart_box.isChecked()

        ok, detail = (
            autostart.enable() if s.autostart_intent else autostart.disable()
        )
        if not ok:
            self.backup_status.setText(f"自启设置失败：{detail}")
            self.backup_status.setStyleSheet(f"color:{self.t.c('danger')}")

        if s.backup_dir:
            provider = LocalBackupProvider(s.backup_dir, [self.paths.db, self.paths.config])
            s.last_backup_ok = provider.snapshot() is not None
        save_settings(self.paths, s)
        self.on_theme_change(s.theme_mode)
        self.accept()


def _wrap(layout) -> QWidget:
    host = QWidget()
    host.setLayout(layout)
    return host
