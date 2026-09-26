# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path


project_root = Path(SPECPATH).parent
version_file = Path(os.environ.get('AURELIA_VERSION_FILE', '')).resolve()
if not version_file.is_file():
    raise RuntimeError('AURELIA_VERSION_FILE must identify the generated Windows version resource')
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
    version=str(version_file),
)
coll = COLLECT(
    exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name='Aurelia',
)
