@echo off
setlocal
title Signature AURELIA FEWURA
cd /d "%~dp0"

set SETUP=installer\output\AURELIA_Setup_5.0.1.exe

if not exist "%SETUP%" (
    echo Installateur introuvable : %SETUP%
    pause
    exit /b 1
)

where signtool >nul 2>nul
if errorlevel 1 (
    echo SignTool n'est pas disponible.
    echo Installez le Windows SDK contenant SignTool.
    pause
    exit /b 1
)

if "%FEWURA_CERT_SHA1%"=="" (
    echo Variable FEWURA_CERT_SHA1 absente.
    echo Configurez l'empreinte SHA1 du certificat de signature de code FEWURA.
    pause
    exit /b 1
)

signtool sign ^
  /sha1 "%FEWURA_CERT_SHA1%" ^
  /fd SHA256 ^
  /tr http://timestamp.digicert.com ^
  /td SHA256 ^
  "%SETUP%"

if errorlevel 1 (
    echo ECHEC DE SIGNATURE.
    pause
    exit /b 1
)

signtool verify /pa /v "%SETUP%"
if errorlevel 1 (
    echo LA VERIFICATION DE SIGNATURE A ECHOUE.
    pause
    exit /b 1
)

echo Signature valide.
pause
