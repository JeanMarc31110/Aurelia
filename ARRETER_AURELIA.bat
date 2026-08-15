@echo off
echo Fermeture d'AURELIA...
taskkill /F /IM python.exe >nul 2>nul
taskkill /F /IM pythonw.exe >nul 2>nul
echo AURELIA arretee.
pause
