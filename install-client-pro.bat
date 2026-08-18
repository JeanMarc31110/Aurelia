@echo off
REM Aurelia Professional Windows Installer Wrapper
REM This batch script calls the PowerShell installer script

setlocal enabledelayedexpansion

REM Get the directory where this batch file is located
set SCRIPT_DIR=%~dp0

REM Check if PowerShell script exists
if not exist "%SCRIPT_DIR%install-client-pro.ps1" (
    echo Error: install-client-pro.ps1 not found in %SCRIPT_DIR%
    exit /b 1
)

REM Run PowerShell script with appropriate execution policy
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install-client-pro.ps1" %*

REM Capture exit code
set EXIT_CODE=!ERRORLEVEL!

REM Exit with the same code as PowerShell
exit /b !EXIT_CODE!
