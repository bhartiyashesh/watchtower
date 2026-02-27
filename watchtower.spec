# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for WatchTower (Smart Lock System).

Usage:
    pyinstaller watchtower.spec

Produces a --onedir bundle at dist/watchtower/ containing the executable
plus bundled templates, YOLO model, and static assets.
"""

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("dashboard/templates", "dashboard/templates"),
        ("setup/templates", "setup/templates"),
        ("yolo11n.pt", "."),
        ("transparent_drawing.png", "."),
        ("setup_hosts.py", "."),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "blinkpy",
        "blinkpy.blinkpy",
        "blinkpy.auth",
        "blinkpy.helpers",
        "blinkpy.helpers.util",
        "telegram",
        "telegram.ext",
        "ultralytics",
        "face_recognition",
        "ring_doorbell",
        "ring_doorbell.listen",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "notebook",
        "IPython",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="watchtower",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="watchtower",
)
