@echo off
title AURELIA V5
cd /d %~dp0
if not exist .env copy .env.example .env
if not exist .venv py -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
start http://127.0.0.1:8000
uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
