@echo off
echo Starting GHL Reporting Dashboard...
cd /d "%~dp0backend"
start "" http://127.0.0.1:5000
"C:\Users\hi\AppData\Local\Programs\Python\Python310\python.exe" app.py
pause
