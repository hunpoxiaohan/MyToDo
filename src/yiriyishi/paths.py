import os
import sys
from pathlib import Path

APP_DIR_NAME = "一日一事"


def _platform_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


class Paths:
    """数据目录解析。源码运行与 PyInstaller frozen 都走同一套逻辑。"""

    def __init__(self, override: str | os.PathLike | None = None):
        self.root = Path(override) if override else _platform_dir() / APP_DIR_NAME
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    @property
    def db(self) -> Path:
        return self.root / "data.db"

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def draft(self) -> Path:
        return self.root / "draft.json"

    @property
    def backups(self) -> Path:
        return self.root / "backups"


def asset_dir() -> Path:
    """assets 目录：frozen 时由 PyInstaller 解到 sys._MEIPASS。"""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "assets"
    return Path(__file__).resolve().parent.parent.parent / "assets"
