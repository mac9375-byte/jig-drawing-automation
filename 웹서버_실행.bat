@echo off
cd /d "%~dp0"
start "" http://127.0.0.1:8000
python -X utf8 web_app.py
pause
