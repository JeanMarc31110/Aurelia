# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all


project_root = Path(SPECPATH).parent
datas = [
    (str(project_root / 'app' / 'templates'), 'app/templates'),
    (str(project_root / 'app' / 'static'), 'app/static'),
    (str(project_root / 'config'), 'config'),
    (str(project_root / 'resources' / 'tesseract'), 'resources/tesseract'),
    (str(project_root / 'VERSION.txt'), '.'),
    (str(project_root / 'THIRD_PARTY_NOTICES.txt'), '.'),
    (str(project_root / 'README_INSTALLATION.txt'), '.'),
]
hiddenimports = [
    'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'multipart',
]

for package in ['pypdfium2', 'PIL', 'pytesseract', 'lxml', 'reportlab']:
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    hiddenimports += package_hidden

a = Analysis(
    [str(project_root / 'aurelia_launcher.py')],
    pathex=[str(project_root)],
    binaries=[], datas=datas, hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='Aurelia', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    disable_windowed_traceback=False, argv_emulation=False, icon=None,
)
coll = COLLECT(
    exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name='Aurelia',
)
