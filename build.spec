# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件、无控制台、带图标。

winrt / windows_toasts / plyer 都靠动态导入定位平台后端，
默认分析会漏掉，必须 collect_all 整体收进来。
"""
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("winrt", "windows_toasts", "plyer"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

datas += [("assets/app.png", "assets"), ("assets/app.ico", "assets")]

a = Analysis(
    ["entry.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "PySide6.QtQml", "PySide6.QtQuick"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="yiriyishi",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/app.ico",
)
