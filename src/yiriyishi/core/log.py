from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from ..paths import Paths


def setup_logging(paths: Paths, debug: bool = False) -> logging.Logger:
    root = logging.getLogger("yiriyishi")
    if root.handlers:
        return root
    root.setLevel(logging.DEBUG)
    root.propagate = False

    file_handler = RotatingFileHandler(
        paths.log_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    stream = logging.StreamHandler()
    stream.setLevel(logging.DEBUG if debug else logging.WARNING)
    stream.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(stream)
    return root


def install_exception_logger() -> None:
    """打包成 GUI 进程后没有控制台：未捕获异常只会被 Qt 的 C++ 调用方吞掉。

    接进 sys.excepthook 写进日志，否则「点了没反应」这类故障查无可查。
    """
    root = logging.getLogger("yiriyishi")
    default = sys.__excepthook__

    def hook(kind, value, tb):
        root.critical("未捕获异常", exc_info=(kind, value, tb))
        default(kind, value, tb)

    sys.excepthook = hook
