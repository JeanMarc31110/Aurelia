@echo off
setlocal
cd /d "%~dp0"

if not exist VERSION.txt (
  echo VERSION.txt introuvable.
  exit /b 1
)
set /p AURELIA_VERSION=<VERSION.txt
set "SETUP=release\Aurelia-Setup-%AURELIA_VERSION%.exe"

if not exist "%SETUP%" (
  echo Installateur introuvable : %SETUP%
  exit /b 1
)
where signtool.exe >nul 2>nul || (
  echo SignTool est indisponible. Installez le Windows SDK officiel.
  exit /b 1
)
if "%FEWURA_CERT_SHA1%"=="" (
  echo FEWURA_CERT_SHA1 est absente. Aucun certificat ne sera invente ou genere.
  exit /b 1
)

signtool.exe sign /sha1 "%FEWURA_CERT_SHA1%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "%SETUP%" || exit /b 1
signtool.exe verify /pa /v "%SETUP%" || exit /b 1
echo Signature Authenticode valide : %SETUP%
