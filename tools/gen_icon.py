"""生成应用图标：assets/app.png（悬浮球/托盘用）与 assets/app.ico（exe 用）。

不依赖外部图片资源，纯 QPainter 绘制，保证打包前可重现。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent / "assets"
ACCENT = "#8A9A5B"


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)

    margin = size // 12
    radius = size // 5
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT))
    p.drawRoundedRect(margin, margin, size - 2 * margin, size - 2 * margin, radius, radius)

    # 一笔勾：表示「一事完成」
    pen = QPen(QColor("#FFFFFF"))
    pen.setWidthF(size * 0.09)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawLine(int(size * 0.27), int(size * 0.53), int(size * 0.44), int(size * 0.70))
    p.drawLine(int(size * 0.44), int(size * 0.70), int(size * 0.74), int(size * 0.33))
    p.end()
    return image


def main() -> int:
    QApplication(sys.argv)  # QImage 之外的绘制需要 QGuiApplication 存在
    ROOT.mkdir(parents=True, exist_ok=True)
    png = render(256)
    if not png.save(str(ROOT / "app.png")):
        print("写 app.png 失败", file=sys.stderr)
        return 1
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(QPixmap.fromImage(render(size)))
    if not icon.pixmap(256).save(str(ROOT / "app.ico")):
        print("写 app.ico 失败（Qt 可能不支持 ico 写出，可用 ImageMagick 替代）", file=sys.stderr)
        return 1
    print(f"图标已生成：{ROOT / 'app.png'} / {ROOT / 'app.ico'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
