from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from yiriyishi.core import autostart
from yiriyishi.core.db import connect, init_db
from yiriyishi.core.repository import TaskRepository
from yiriyishi.core.sync import HttpSyncProvider, LocalBackupProvider, export_json, import_json


# ---------------- 开机自启：只验命令构造，绝不碰真注册表 ----------------
def test_launch_command_in_source_mode_uses_module_flag():
    """开发期自启必须走 `pythonw -m yiriyishi`，直接指脚本会因相对导入而崩。"""
    if getattr(sys, "frozen", False):
        pytest.skip("打包后指向 exe，本用例只验源码模式")
    cmd = autostart.launch_command()
    assert cmd[1:] == ["-m", "yiriyishi"]
    assert Path(cmd[0]).is_absolute()


def test_dry_run_reports_what_would_be_written(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    ok, marker = autostart.enable(dry_run=True)
    assert ok and marker.startswith("YiRiYiShi=")
    assert autostart.disable(dry_run=True) == (True, "<dry-run>")


def test_is_enabled_dry_run_never_opens_registry(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("dry-run 不应访问注册表")

    try:
        import winreg

        monkeypatch.setattr(winreg, "OpenKey", boom)
        monkeypatch.setattr(winreg, "CreateKey", boom)
    except ImportError:
        pytest.skip("非 Windows 平台")
    assert autostart.is_enabled(dry_run=True) is True


def test_desktop_entry_content_for_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setattr(autostart.Path, "home", classmethod(lambda cls: tmp_path))
    ok, _ = autostart.enable()
    entry = tmp_path / ".config" / "autostart" / "yiriyishi.desktop"
    assert ok and entry.exists()
    content = entry.read_text(encoding="utf-8")
    assert "Exec=" in content and "[Desktop Entry]" in content
    assert autostart.disable() == (True, "")
    assert not entry.exists()


# ---------------- 备份 ----------------
def test_local_backup_writes_timestamped_snapshot(tmp_path):
    data = tmp_path / "data.db"
    data.write_text("db-bytes", encoding="utf-8")
    target = tmp_path / "backup"
    provider = LocalBackupProvider(target, [data])

    path = provider.snapshot()
    assert path is not None and path.is_dir()
    assert (path / "data.db").read_text(encoding="utf-8") == "db-bytes"


def test_backup_prunes_to_ten_newest_snapshots(tmp_path):
    target = tmp_path / "backup"
    for i in range(12):
        (target / f"20261009-{i:06d}").mkdir(parents=True)
    LocalBackupProvider(target, [])._prune()
    left = sorted(d.name for d in target.iterdir())
    assert len(left) == 10 and left[-1].endswith("11")


def test_backup_without_dir_is_noop(conn):
    provider = LocalBackupProvider(None, [])
    assert provider.snapshot() is None
    assert provider.push([]) is False


def test_http_provider_is_an_explicit_placeholder():
    """联网同步本期只留接口，调用必须明确报错而不是静默失败。"""
    provider = HttpSyncProvider("https://example.invalid", "token")
    with pytest.raises(NotImplementedError):
        provider.push([])
    with pytest.raises(NotImplementedError):
        provider.pull()


# ---------------- 导入导出 ----------------
def test_export_then_import_roundtrips(repo, tmp_path):
    repo.create(title="导出测试", due_time=datetime(2026, 10, 10, 9), importance=5)
    file = export_json(repo, tmp_path / "tasks.json")
    assert "导出测试" in file.read_text(encoding="utf-8")

    other = connect(tmp_path / "other.db")
    init_db(other)
    target = TaskRepository(other)
    assert import_json(target, file) == 1
    loaded = target.list()[0]
    assert loaded.title == "导出测试"
    assert isinstance(loaded.due_time, datetime)   # 字符串必须还原成 datetime
    other.close()


def test_import_skips_unknown_keys_and_fresh_ids(repo, tmp_path):
    file = Path(tmp_path / "x.json")
    file.write_text(
        json.dumps([{"title": "带野字段", "id": "keep-me", "server_rev": 9}], ensure_ascii=False),
        encoding="utf-8",
    )
    assert import_json(repo, file) == 1
    task = repo.list()[0]
    assert task.title == "带野字段"
    assert task.id != "keep-me"                     # 导入不覆盖本地同 id 记录
