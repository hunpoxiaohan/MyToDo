from __future__ import annotations

import json
from datetime import datetime

import pytest

from yiriyishi.core.config import load_settings, save_settings
from yiriyishi.core.models import AppSettings, PrioritySettings, is_in_quiet_hours


def test_missing_config_writes_default_file(paths):
    s = load_settings(paths)
    assert s.priority.urgent_minutes == 30
    assert paths.config.exists()


def test_roundtrip_keeps_nested_priority(paths):
    s = AppSettings(
        theme_mode="dark",
        priority=PrioritySettings(urgent_minutes=45, warning_hours=6, near_days=2, week_days=5),
        float_ball_pos=(100, 200),
        window_geometry={"x": 1, "y": 2, "w": 3, "h": 4, "maximized": True},
    )
    save_settings(paths, s)
    loaded = load_settings(paths)
    assert loaded.theme_mode == "dark"
    assert loaded.priority.urgent_minutes == 45
    assert loaded.priority.week_days == 5
    assert loaded.float_ball_pos == (100, 200)
    assert loaded.window_geometry["maximized"] is True


def test_partial_file_fills_missing_keys_with_defaults(paths):
    paths.config.write_text(json.dumps({"theme_mode": "light"}), encoding="utf-8")
    s = load_settings(paths)
    assert s.theme_mode == "light"
    assert s.priority.warning_hours == 8           # 缺字段回落到默认值
    assert s.dnd_start == "22:00"


def test_corrupt_config_is_renamed_and_rebuilt(paths):
    paths.config.write_text("{ this is not json", encoding="utf-8")
    s = load_settings(paths)
    assert s.theme_mode == "system"
    assert paths.config.with_suffix(".json.broken").exists()
    assert json.loads(paths.config.read_text(encoding="utf-8"))["theme_mode"] == "system"


def test_no_temp_files_left_after_save(paths):
    for _ in range(3):
        save_settings(paths, AppSettings())
    leftovers = [p.name for p in paths.root.iterdir() if p.name.startswith(".cfg-")]
    assert leftovers == []


def test_unknown_keys_in_file_are_ignored(paths):
    paths.config.write_text(json.dumps({"theme_mode": "dark", "mystery": 1}), encoding="utf-8")
    assert load_settings(paths).theme_mode == "dark"


# ---------------- 免打扰时段 ----------------
@pytest.mark.parametrize(
    "hhmm,start,end,want",
    [
        ("23:30", "22:00", "08:00", True),
        ("07:59", "22:00", "08:00", True),
        ("08:00", "22:00", "08:00", False),
        ("12:00", "22:00", "08:00", False),   # 跨午夜区间的白天
        ("01:00", "22:00", "08:00", True),
        ("13:00", "12:00", "14:00", True),    # 同日区间
        ("15:00", "12:00", "14:00", False),
        ("15:00", "09:00", "09:00", False),   # 起止相同 = 不限制
    ],
)
def test_quiet_hours(hhmm, start, end, want):
    now = datetime.strptime(f"2026-10-09 {hhmm}", "%Y-%m-%d %H:%M")
    assert is_in_quiet_hours(now, start, end) is want
