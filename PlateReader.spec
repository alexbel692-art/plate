# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [("models", "models"), ("resources", "resources")]
binaries = []
hiddenimports = ["openpyxl", "yaml", "PySide6.QtNetwork"]
for pkg in ("fast_alpr", "fast_plate_ocr", "open_image_models", "onnxruntime"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
              "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PlateReader",
    console=False,
    icon="resources/icon.ico",
    version="version.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, name="PlateReader")
