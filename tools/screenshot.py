r"""本地调试用：离屏渲染各界面到 PNG，用来核版式与配色。

用法（PowerShell）：
    $env:PYTHONPATH='src'; $env:QT_QPA_PLATFORM='offscreen'
    .venv\Scripts\python.exe tools\screenshot.py --out .preview
输出：main-*.png（主窗）/ panel-*.png（悬浮面板）/ ball-*.png（悬浮球），深浅色各一套
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from yiriyishi.core.priority import Bucket
from yiriyishi.paths import Paths
from yiriyishi.ui.float_panel import FloatBall, FloatPanel
from yiriyishi.ui.main_window import MainWindow, _groups
from seed_demo import seed


def shoot(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data_dir = out / "data"
    seed(str(data_dir))

    app = QApplication.instance() or QApplication([])
    window = MainWindow(Paths(data_dir))

    for mode in ("light", "dark"):
        window.apply_theme_mode(mode)          # 走真实换肤路径，颜色与线上一致
        t = window.t

        window.resize(1120, 720)
        window.show()
        app.processEvents()
        first = window.list.item(0)
        if first:
            window.list.setCurrentItem(first)
            window._sync_detail()
        app.processEvents()
        window.grab().save(str(out / f"main-{mode}.png"))

        names = {p.id: p.name for p in window.person_repo.list(active_only=False)}
        groups = _groups(window.task_repo.list(), names, datetime.now(),
                         window.settings.priority)
        panel = FloatPanel(t)
        panel.render(groups)
        panel.show()
        app.processEvents()
        panel.grab().save(str(out / f"panel-{mode}.png"))

        ball = FloatBall(t)
        ball.set_count(len(groups.get(Bucket.OVERDUE, [])) + len(groups.get(Bucket.TODAY, [])))
        ball.show()
        app.processEvents()
        ball.grab().save(str(out / f"ball-{mode}.png"))

        panel.deleteLater()
        ball.deleteLater()
        print(f"{mode} → {out / f'main-{mode}.png'}")

    window.quit_app()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./.preview")
    shoot(Path(ap.parse_args().out))
