@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".buildvenv\Scripts\python.exe" (
    echo ERREUR: exécutez d'abord BUILD_WINDOWS_RELEASE.bat.
    exit /b 1
)

".buildvenv\Scripts\python.exe" tools\build_windows_installer.py %*
exit /b %errorlevel%
