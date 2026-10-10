from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("yiriyishi.core.autostart")

APP_NAME = "YiRiYiShi"
WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> list[str]:
    """打包后指向 exe；源码运行指向 python + `python -m yiriyishi`。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    python = Path(sys.executable).with_name("pythonw.exe" if sys.platform == "win32" else "python")
    if not python.exists():
        python = Path(sys.executable)
    return [str(python), "-m", APP_NAME.lower()]


def is_enabled(dry_run: bool = False) -> bool:
    cmd = launch_command()
    if dry_run:
        return bool(_expected_marker(cmd))  # 只报告"将会写入什么"，不读注册表
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, APP_NAME)
            return _same(value, cmd[0])
        except OSError:
            return False
    if sys.platform == "darwin":
        return _macos_registered()
    return (Path.home() / ".config" / "autostart" / f"{APP_NAME.lower()}.desktop").exists()


def enable(dry_run: bool = False) -> tuple[bool, str]:
    cmd = launch_command()
    if dry_run:
        return True, _expected_marker(cmd)
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY) as key:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd[0])
        elif sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to make login item '
                    f'at end with properties {{path:"{cmd[0]}", hidden:false}}',
                ],
                check=True,
                capture_output=True,
            )
        else:
            desktop = Path.home() / ".config" / "autostart" / f"{APP_NAME.lower()}.desktop"
            desktop.parent.mkdir(parents=True, exist_ok=True)
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=一日一事\n"
                f"Exec={' '.join(cmd)}\nX-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
            desktop.chmod(0o755)
        log.info("开机自启已开启：%s", cmd)
        return True, str(cmd)
    except Exception as exc:
        log.error("开启开机自启失败：%s", exc)
        return False, str(exc)


def disable(dry_run: bool = False) -> tuple[bool, str]:
    if dry_run:
        return True, "<dry-run>"
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
        elif sys.platform == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to delete login item "YiRiYiShi"',
                ],
                check=False,
                capture_output=True,
            )
        else:
            (Path.home() / ".config" / "autostart" / f"{APP_NAME.lower()}.desktop").unlink(missing_ok=True)
        log.info("开机自启已关闭")
        return True, ""
    except FileNotFoundError:
        return True, ""  # 本来就没注册
    except Exception as exc:
        log.error("关闭开机自启失败：%s", exc)
        return False, str(exc)


def _macos_registered() -> bool:
    try:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get the name of every login item'],
            capture_output=True, text=True, check=False,
        ).stdout
        return APP_NAME in out or "一日一事" in out
    except Exception:
        return False


def _same(registered: str, exe: str) -> bool:
    return os.path.normcase(registered.strip('"')) == os.path.normcase(exe.strip('"'))


def _expected_marker(cmd: list[str]) -> str:
    return f"{APP_NAME}={cmd[0]}"
