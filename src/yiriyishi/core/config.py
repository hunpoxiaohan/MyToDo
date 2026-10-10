from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from .models import AppSettings
from ..paths import Paths

log = logging.getLogger("yiriyishi.core.config")


def load_settings(paths: Paths) -> AppSettings:
    path = paths.config
    if not path.exists():
        save_settings(paths, AppSettings())
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config root is not an object")
        return AppSettings.from_dict(data)
    except Exception as exc:
        backup = path.with_suffix(".json.broken")
        try:
            os.replace(path, backup)
        except OSError:
            path.unlink(missing_ok=True)
        log.error("config.json 损坏，已重建默认值：%s（坏文件：%s）", exc, backup)
        fresh = AppSettings()
        save_settings(paths, fresh)
        return fresh


def save_settings(paths: Paths, settings: AppSettings) -> None:
    """原子写：先写同目录临时文件再 os.replace，避免崩溃留下半截 JSON。"""
    path = paths.config
    payload = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".cfg-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise
