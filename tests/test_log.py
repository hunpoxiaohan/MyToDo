from __future__ import annotations

import sys

from yiriyishi.core.log import install_exception_logger, setup_logging


def test_uncaught_exceptions_reach_the_logfile(paths):
    """GUI 包没有控制台：异常只写 stderr 就等于没写。

    这条同时是「点了没反应」类故障的唯一线索来源，所以测的是文件内容而不是 logger 调用。
    """
    previous = sys.excepthook
    setup_logging(paths)
    install_exception_logger()
    try:
        try:
            raise ValueError("槽函数里炸了一下")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        text = (paths.log_dir / "app.log").read_text(encoding="utf-8")
        assert "未捕获异常" in text
        assert "槽函数里炸了一下" in text
        assert "Traceback" in text
    finally:
        sys.excepthook = previous
