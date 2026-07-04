# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller ONE-FILE spec for the Employee Shift Tracker.

Produces dist/EmployeeShiftTracker.exe — a single self-contained console exe.

Key points:
* datas bundles the Jinja templates and static assets INTO the exe. At runtime
  they are unpacked to _MEIPASS and loaded from there (see app/paths.py). The
  /data folder is NOT bundled — it is created next to the exe at runtime so
  payroll data persists.
* hiddenimports: uvicorn/anyio load their implementation submodules dynamically,
  and passlib loads its bcrypt backend dynamically, so PyInstaller can't see them
  by static analysis — we collect them explicitly.
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("anyio")
    + [
        "passlib.handlers.bcrypt",
        "bcrypt",
        "multipart",         # python-multipart (form parsing)
        "python_multipart",
    ]
)

datas = [
    ("app/templates", "templates"),   # -> _MEIPASS/templates
    ("app/static", "static"),         # -> _MEIPASS/static
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],  # not used; trims size
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EmployeeShiftTracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # console shows the URL and stops the server on close
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
