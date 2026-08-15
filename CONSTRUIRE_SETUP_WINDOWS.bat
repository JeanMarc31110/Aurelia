@echo off
setlocal EnableExtensions
title Construction AURELIA Windows
cd /d "%~dp0"

echo ==========================================
echo       BUILD AURELIA WINDOWS PRO
echo ==========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERREUR : Python 3.11+ est requis.
        pause
        exit /b 1
    )
    set PY=python
)

if not exist ".buildvenv" (
    %PY% -m venv .buildvenv
    if errorlevel 1 goto :error
)

call ".buildvenv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto :error

pip install -r requirements-build.txt
if errorlevel 1 goto :error

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

pyinstaller --noconfirm installer\aurelia.spec
if errorlevel 1 goto :error

set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe

if not defined ISCC (
    echo.
    echo Inno Setup 6 n'est pas installe.
    echo Installez Inno Setup, puis relancez ce script.
    start https://jrsoftware.org/isdl.php
    pause
    exit /b 2
)

"%ISCC%" installer\Aurelia.iss
if errorlevel 1 goto :error

echo.
echo ==========================================
echo BUILD TERMINE
echo Installateur :
echo installer\output\AURELIA_Setup_5.0.1.exe
echo ==========================================
pause
exit /b 0

:error
echo.
echo ECHEC DE CONSTRUCTION.
pause
exit /b 1
