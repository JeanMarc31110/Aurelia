@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo ERREUR: le lanceur Python py est requis.
    exit /b 1
)

if not exist ".buildvenv\Scripts\python.exe" (
    py -3.14 -m venv .buildvenv
    if errorlevel 1 exit /b 1
)

".buildvenv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-build.txt
if errorlevel 1 exit /b 1

".buildvenv\Scripts\python.exe" tools\build_windows_release.py %*
exit /b %errorlevel%
