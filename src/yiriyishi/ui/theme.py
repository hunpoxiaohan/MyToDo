from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from ..paths import asset_dir

# 唯一的颜色真相源：控件一律通过 token() 取色，禁止硬编码 hex。
LEVEL_COLORS = {
    "light": {
        "overdue": "#E74C3C",
        "urgent": "#E74C3C",
        "warning": "#F39C12",
        "today": "#8A9A5B",
        "near": "#F39C12",
        "week": "#A8B88A",
        "future": "#95A5A6",
        "normal": "#95A5A6",
        "done": "#B9C0C4",
    },
    "dark": {
        "overdue": "#FF6B5B",
        "urgent": "#FF6B5B",
        "warning": "#FFB84D",
        "today": "#A8B88A",
        "near": "#FFB84D",
        "week": "#8A9A5B",
        "future": "#7F8C8D",
        "normal": "#7F8C8D",
        "done": "#5A6165",
    },
}

PALETTES = {
    # 暖中性：奶油底 + 燕麦层级 + 暖棕文字；品牌色仍是偏暖的鼠尾草绿。
    # 优先级语义色（红/黄/灰/绿）由 LEVEL_COLORS 单独管，不随主题变。
    "light": {
        "bg": "#FAF7F2", "surface": "#FFFDFA", "surface_alt": "#F3EDE4",
        "hover": "#EDE5D9", "text": "#332F2A", "text_muted": "#8B8378",
        "border": "#E8DFD2", "accent": "#8A9A5B", "accent_soft": "#E9EEDC",
        "accent_text": "#FFFFFF", "danger": "#E74C3C",
    },
    "dark": {
        # 暖炭底，鼠尾草绿提亮一档保证文字对比
        "bg": "#211D18", "surface": "#2A251F", "surface_alt": "#332D26",
        "hover": "#3D362D", "text": "#EFE8DE", "text_muted": "#A69D8F",
        "border": "#3C352C", "accent": "#A3B373", "accent_soft": "#3A4030",
        "accent_text": "#171510", "danger": "#FF6B5B",
    },
}

MONO_FAMILIES = ["Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "monospace"]
CJK_FAMILIES = ["Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "sans-serif"]


@dataclass
class Tokens:
    mode: str
    colors: dict
    levels: dict

    def c(self, key: str) -> str:
        return self.colors[key]

    def level(self, name: str) -> str:
        return self.levels.get(name, self.levels["normal"])


def tokens(mode: str) -> Tokens:
    mode = mode if mode in PALETTES else "light"
    return Tokens(mode=mode, colors=dict(PALETTES[mode]), levels=dict(LEVEL_COLORS[mode]))


def _palette(t: Tokens) -> QPalette:
    """QPalette 同步取色：原生控件（日历弹窗、菜单、滚动条）不走 QSS，靠它兜底。"""
    p = QPalette()
    c = t.colors
    p.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    p.setColor(QPalette.ColorRole.Base, QColor(c["surface"]))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(c["surface_alt"]))
    p.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.Button, QColor(c["surface"]))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accent_text"]))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["text_muted"]))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["surface"]))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    p.setColor(QPalette.ColorRole.BrightText, QColor(c["danger"]))
    p.setColor(QPalette.ColorRole.Light, QColor(c["surface_alt"]))
    p.setColor(QPalette.ColorRole.Mid, QColor(c["border"]))
    p.setColor(QPalette.ColorRole.Dark, QColor(c["border"]))
    return p


def ui_font(point: float = 10, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies([f for f in CJK_FAMILIES])
    font.setPointSizeF(point)
    font.setBold(bold)
    return font


def mono_font(point: float = 10) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES)
    font.setPointSizeF(point)
    return font


def resolve_mode(setting: str) -> str:
    if setting == "light":
        return "light"
    if setting == "dark":
        return "dark"
    hints = QApplication.instance().styleHints()
    return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"


def _mix(color: str, other: str, ratio: float) -> str:
    """向 other 方向线性混合，ratio=0 保持原色。Qt QSS 只认 #RRGGBB/#AARRGGBB，
    所以半透明色必须预先算成实色。"""
    a, b = QColor(color), QColor(other)
    out = QColor(
        round(a.red() + (b.red() - a.red()) * ratio),
        round(a.green() + (b.green() - a.green()) * ratio),
        round(a.blue() + (b.blue() - a.blue()) * ratio),
    )
    return out.name()


def build_qss(t: Tokens) -> str:
    """柔和留白路线：大圆角 + 极浅描边 + 分组卡片，不靠阴影靠层次。"""
    c = t.c
    accent_hover = _mix(c("accent"), c("text"), 0.14)
    overdue_bg = _mix(c("surface"), c("danger"), 0.10)
    # Qt 只在 QSS 给到真实图片时才画 ::down-arrow，光靠边框拼三角会渲成方块
    chevron = (asset_dir() / f"chevron-{t.mode}.png").as_posix()
    return f"""
QWidget {{ background:{c('bg')}; color:{c('text')}; font-size:13px; }}
QMainWindow, QDialog {{ background:{c('bg')}; }}

#Card, #ToastBanner, #OverdueBanner {{
    background:{c('surface')}; border:1px solid {c('border')}; border-radius:14px;
}}
#ToastBanner {{ border:1px solid {c('hover')}; }}
#OverdueBanner {{ background:{overdue_bg}; border:1px solid {c('danger')}; }}
/* FloatPanel 的卡片由 float_panel.paintEvent 自己画：顶层透明窗口靠样式表会画不出底色 */

QLabel {{ background:transparent; }}
#Muted {{ color:{c('text_muted')}; font-size:12px; background:transparent; }}
#SectionTitle {{
    color:{c('text_muted')}; font-size:11px; font-weight:600; background:transparent;
}}

/* ---------- 按钮 ---------- */
QPushButton#Primary {{
    background:{c('accent')}; color:{c('accent_text')}; border:none;
    border-radius:11px; padding:8px 18px; font-weight:600;
}}
QPushButton#Primary:hover {{ background:{accent_hover}; }}
QPushButton#Primary:pressed {{ background:{c('accent_soft')}; color:{c('text')}; }}
QPushButton {{
    background:{c('surface')}; color:{c('text')}; border:1px solid {c('border')};
    border-radius:10px; padding:6px 14px;
}}
QPushButton:hover {{ background:{c('surface_alt')}; border:1px solid {c('hover')}; }}
QPushButton:pressed {{ background:{c('hover')}; }}
QPushButton:disabled {{ color:{c('text_muted')}; background:{c('surface_alt')}; }}
QPushButton#Ghost {{
    background:transparent; border:none; color:{c('text_muted')}; padding:6px 10px;
}}
QPushButton#Ghost:hover {{ color:{c('text')}; background:{c('surface_alt')}; border-radius:10px; }}
QPushButton#Ghost:checked {{ color:{c('accent_text')}; background:{c('accent')}; }}
/* 破坏性动作：平时只是红字，按下才铺红，免得一排按钮里有个太抢眼的 */
QPushButton#Danger {{
    background:transparent; border:1px solid {c('border')}; color:{c('danger')};
}}
QPushButton#Danger:hover {{ background:{c('danger')}; border:1px solid {c('danger')}; color:{c('accent_text')}; }}
/* 逾期横幅里的出口：Ghost 的灰字压在浅红底上像被禁用了，给它一圈红描边 */
QPushButton#BannerAction {{
    background:transparent; border:1px solid {c('danger')}; border-radius:10px;
    color:{c('danger')}; padding:5px 12px;
}}
QPushButton#BannerAction:hover {{ background:{c('danger')}; color:{c('accent_text')}; }}

/* ---------- 分类 chip ---------- */
#CategoryChip {{
    background:transparent; border:1px solid {c('border')};
    border-radius:15px; padding:5px 14px; color:{c('text_muted')};
}}
#CategoryChip:hover {{ border:1px solid {c('hover')}; color:{c('text')}; }}
#CategoryChip[active="true"] {{
    background:{c('accent')}; border:1px solid {c('accent')};
    color:{c('accent_text')}; font-weight:600;
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QDateTimeEdit, QComboBox, QSpinBox {{
    background:{c('surface')}; color:{c('text')}; border:1px solid {c('border')};
    border-radius:10px; padding:6px 10px; selection-background-color:{c('accent')};
    selection-color:{c('accent_text')};
}}
QLineEdit:focus, QTextEdit:focus, QDateTimeEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border:1px solid {c('accent')}; background:{c('surface')};
}}
QComboBox::drop-down {{ border:none; width:20px; }}
QDateTimeEdit::drop-down, QDateEdit::drop-down, QTimeEdit::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right;
    border:none; width:18px; background:transparent;
}}
QComboBox::down-arrow, QDateTimeEdit::down-arrow, QDateEdit::down-arrow,
QTimeEdit::down-arrow {{
    image: url("{chevron}"); width:14px; height:14px;
}}
QComboBox QAbstractItemView {{
    background:{c('surface')}; border:1px solid {c('border')}; border-radius:10px;
    selection-background-color:{c('accent_soft')}; selection-color:{c('text')}; outline:none;
}}
QCheckBox, QRadioButton {{ background:transparent; spacing:8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width:16px; height:16px; }}

/* ---------- 列表 ---------- */
QListView, QTreeView {{ background:transparent; border:none; outline:none; }}
QListView::item {{ background:transparent; }}
QListView::item:selected {{ background:transparent; }}
QSplitter::handle {{ background:transparent; }}
QSplitter::handle:horizontal {{ width:10px; }}

/* ---------- 设置分组 ---------- */
QGroupBox {{
    background:{c('surface')}; border:1px solid {c('border')}; border-radius:14px;
    margin-top:14px; padding:14px 12px 12px 12px; font-weight:600;
}}
QGroupBox::title {{
    subcontrol-origin:margin; left:14px; padding:0 6px;
    color:{c('text_muted')}; background:{c('bg')};
}}

/* ---------- 悬浮面板行 ---------- */
#PanelRow {{ background:{c('surface_alt')}; border:1px solid transparent; border-radius:10px; }}
#PanelRow:hover {{ background:{c('hover')}; border:1px solid {c('accent_soft')}; }}
#RowDone {{
    background:transparent; border:1px solid {c('border')}; border-radius:12px;
    color:{c('text_muted')}; font-size:12px; padding:0 10px; margin:0;
}}
#RowDone:hover {{ background:{c('accent')}; border:1px solid {c('accent')}; color:{c('accent_text')}; }}

/* ---------- 详情：完成时间胶囊 ---------- */
#Capsule {{
    background:transparent; border:1px solid {c('border')};
    border-radius:14px; padding:5px 12px; color:{c('text_muted')};
}}
#Capsule:hover {{ border:1px solid {c('hover')}; color:{c('text')}; }}
#Capsule:checked {{
    background:{c('accent')}; border:1px solid {c('accent')};
    color:{c('accent_text')}; font-weight:600;
}}
#Capsule:disabled {{ color:{c('text_muted')}; border:1px solid {c('border')}; }}

/* ---------- 记录时间线 ---------- */
#NoteList {{ background:transparent; border:none; }}
#NoteRow {{ background:{c('surface_alt')}; border:1px solid transparent; border-radius:10px; }}
#NoteRow:hover {{ background:{c('hover')}; border:1px solid {c('accent_soft')}; }}
#NoteBody {{ background:transparent; border:none; }}
#NoteAction, #NoteDelete {{
    background:transparent; border:1px solid {c('border')}; border-radius:8px;
    color:{c('text_muted')}; font-size:11px; padding:0; min-width:26px; min-height:20px;
}}
#NoteAction:hover {{ background:{c('accent')}; border:1px solid {c('accent')}; color:{c('accent_text')}; }}
#NoteDelete:hover {{ background:{c('danger')}; border:1px solid {c('danger')}; color:{c('accent_text')}; }}
#NoteEditor {{ border-radius:8px; padding:4px 8px; }}

/* ---------- 滚动条 / 菜单 / 提示 ---------- */
QScrollBar:vertical {{ background:transparent; width:9px; margin:2px; }}
QScrollBar::handle:vertical {{ background:{c('border')}; border-radius:4px; min-height:26px; }}
QScrollBar::handle:vertical:hover {{ background:{c('hover')}; }}
QScrollBar:horizontal {{ background:transparent; height:9px; margin:2px; }}
QScrollBar::handle:horizontal {{ background:{c('border')}; border-radius:4px; min-width:26px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
QMenu {{
    background:{c('surface')}; border:1px solid {c('border')}; border-radius:12px; padding:6px;
}}
QMenu::item {{ padding:6px 18px; border-radius:8px; background:transparent; }}
QMenu::item:selected {{ background:{c('accent_soft')}; color:{c('text')}; }}
QToolTip {{
    background:{c('surface')}; color:{c('text')}; border:1px solid {c('border')};
    border-radius:8px; padding:4px 8px;
}}
"""


def apply_theme(app: QApplication, t: Tokens) -> None:
    app.setStyleSheet(build_qss(t))
    app.setPalette(_palette(t))
    app.setFont(ui_font())


def refresh_tokens(t: Tokens, mode: str) -> Tokens:
    """原地换色：控件持有同一个 Tokens 引用，热切换不必重建界面。"""
    fresh = tokens(mode)
    t.mode = fresh.mode
    t.colors.clear()
    t.colors.update(fresh.colors)
    t.levels.clear()
    t.levels.update(fresh.levels)
    return t
