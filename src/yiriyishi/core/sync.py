from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from .models import Task, parse_dt
from .repository import TaskRepository

log = logging.getLogger("yiriyishi.core.sync")

_KEEP = 10


class SyncProvider:
    name = "base"

    def push(self, tasks: list[Task]) -> bool:
        raise NotImplementedError

    def pull(self) -> list[Task]:
        raise NotImplementedError


class LocalBackupProvider(SyncProvider):
    """把 DB + config 拷进指定目录（可指向坚果云/OneDrive 同步夹）。离线也能用。"""

    name = "local"

    def __init__(self, backup_dir: Path | str | None, data_files: list[Path]):
        self.backup_dir = Path(backup_dir) if backup_dir else None
        self.data_files = [Path(p) for p in data_files]

    def snapshot(self) -> Path | None:
        if not self.backup_dir:
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / stamp
        target.mkdir(parents=True, exist_ok=True)
        for f in self.data_files:
            if f.exists():
                shutil.copy2(f, target / f.name)
        self._prune()
        log.info("本地备份完成：%s", target)
        return target

    def _prune(self) -> None:
        dirs = sorted(
            [d for d in self.backup_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )
        for stale in dirs[_KEEP:]:
            shutil.rmtree(stale, ignore_errors=True)

    def push(self, tasks: list[Task]) -> bool:
        return self.snapshot() is not None

    def pull(self) -> list[Task]:
        return []


class HttpSyncProvider(SyncProvider):
    """预留：未来做多端同步时实现。本期不接网络。"""

    name = "http"

    def __init__(self, endpoint: str = "", token: str = ""):
        self.endpoint = endpoint
        self.token = token

    def push(self, tasks: list[Task]) -> bool:
        raise NotImplementedError("HTTP 同步属于 V2，本期只提供接口")

    def pull(self) -> list[Task]:
        raise NotImplementedError("HTTP 同步属于 V2，本期只提供接口")


def export_json(repo: TaskRepository, path: Path) -> Path:
    payload = [t.to_public_dict() for t in repo.list(include_archived=True, collapse_recurring=False)]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


_DT_FIELDS = {"due_time", "created_at", "completed_at"}


def import_json(repo: TaskRepository, path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    known = set(Task.__dataclass_fields__)
    count = 0
    for item in data:
        # 丢弃 id：导入的条目在本地一律是新任务，避免覆盖同 id 记录
        kwargs = {k: v for k, v in item.items() if k in known and k != "id"}
        for key in _DT_FIELDS & kwargs.keys():
            kwargs[key] = parse_dt(kwargs[key])
        repo.create(**kwargs)
        count += 1
    return count
