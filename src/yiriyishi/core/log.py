from __future__ import annotations

import logging
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
