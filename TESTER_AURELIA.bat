@echo off
setlocal
title Test AURELIA V5
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo AURELIA n'est pas installee. Lancez INSTALLER_AURELIA.bat d'abord.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -c "from app.db import init_db; init_db(); from app.services.orchestrator import process_invoice; r=process_invoice({'direction':'purchase','invoice_number':'SELFTEST-001','issue_date':'2026-08-15','supplier':{'name':'Test Telecom'},'customer':{'name':'Test Client'},'lines':[{'description':'abonnement internet'}],'net_amount':100,'vat_amount':20,'gross_amount':120,'currency':'EUR'},'selftest'); print('AURELIA TEST OK - statut:',r['status'],'- compte:',r['accounting_proposal']['account'])"
if errorlevel 1 (
    echo.
    echo TEST ECHEC.
    pause
    exit /b 1
)

echo.
echo TEST REUSSI.
pause
