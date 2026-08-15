@echo off
setlocal
title Installation AURELIA V5
cd /d "%~dp0"

echo ==========================================
echo        INSTALLATION AURELIA V5
echo ==========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set PY_CMD=py
    goto :python_ok
)

where python >nul 2>nul
if %errorlevel%==0 (
    set PY_CMD=python
    goto :python_ok
)

echo Python n'est pas installe ou n'est pas accessible.
echo.
echo AURELIA necessite Python 3.11 ou superieur.
echo Le navigateur va ouvrir la page officielle de Python.
echo Installez Python en cochant "Add Python to PATH",
echo puis relancez INSTALLER_AURELIA.bat.
echo.
start https://www.python.org/downloads/windows/
pause
exit /b 1

:python_ok
echo Python detecte.
echo.

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Fichier .env cree.
)

if not exist ".venv" (
    echo Creation de l'environnement Python...
    %PY_CMD% -m venv .venv
    if errorlevel 1 goto :error
)

echo Activation de l'environnement...
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :error

echo Mise a jour de pip...
python -m pip install --upgrade pip
if errorlevel 1 goto :error

echo Installation des dependances AURELIA...
pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo ==========================================
echo Installation terminee avec succes.
echo ==========================================
echo.
echo Utilisateur initial : admin
echo Mot de passe initial : Aurelia-ChangeMe!
echo.
echo IMPORTANT : changez ce mot de passe avant usage reel.
echo.
echo Pour lancer AURELIA :
echo double-cliquez sur LANCER_AURELIA.bat
echo.
pause
exit /b 0

:error
echo.
echo Une erreur est survenue pendant l'installation.
echo Verifiez votre connexion Internet et votre installation Python.
pause
exit /b 1
