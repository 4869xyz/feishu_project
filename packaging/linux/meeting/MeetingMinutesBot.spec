# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project_root = Path(SPECPATH).resolve().parents[2]

datas = []
binaries = []
hiddenimports = []
# rapidocr ships ONNX models; onnxruntime and pymupdf need native libs;
# tzdata keeps Asia/Shanghai resolvable under zoneinfo.
for package_name in (
    "lark_channel",
    "rapidocr",
    "onnxruntime",
    "pymupdf",
    "tzdata",
    "docxtpl",
    "markdown_it",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)

hiddenimports.extend(
    [
        "aiosqlite",
        "sqlalchemy.dialects.sqlite.aiosqlite",
        "jinja2",
        "onnxruntime.capi",
    ]
)

analysis = Analysis(
    [str(project_root / "run_meeting_minutes_bot.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="MeetingMinutesBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MeetingMinutesBot",
)
