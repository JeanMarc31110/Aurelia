@echo off
setlocal
title AURELIA V5
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo AURELIA n'est pas encore installee.
    echo Lancement de l'installateur...
    call INSTALLER_AURELIA.bat
    if errorlevel 1 (
        echo.
        echo L'installation a echoue. AURELIA ne sera pas lancee.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\activate.bat" (
    echo Environnement Python introuvable.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Impossible d'activer l'environnement Python.
    pause
    exit /b 1
)

start "" http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

pause
